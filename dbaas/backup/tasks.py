# -*- coding: utf-8 -*-

from dbaas.celery import app
from util.decorators import only_one
from physical.models import DatabaseInfra, Plan, Instance
from logical.models import Database
from dbaas_nfsaas.provider import NfsaasProvider
from models import Snapshot
from notification.models import TaskHistory
from system.models import Configuration
import datetime
from datetime import date, timedelta
from util import exec_remote_command
from dbaas_cloudstack.models import HostAttr as Cloudstack_HostAttr
from util import get_worker_name
import logging
LOG = logging.getLogger(__name__)


def set_backup_error(databaseinfra, snapshot, errormsg):
    LOG.error(errormsg)
    snapshot.status = Snapshot.ERROR
    snapshot.error = errormsg
    snapshot.size = 0
    snapshot.end_at = datetime.datetime.now()
    snapshot.purge_at = datetime.datetime.now()
    snapshot.save()
    register_backup_dbmonitor(databaseinfra, snapshot)

def register_backup_dbmonitor(databaseinfra, snapshot):
    try:
        from dbaas_dbmonitor.provider import DBMonitorProvider
        DBMonitorProvider().register_backup(databaseinfra = databaseinfra,
                                            start_at = snapshot.start_at,
                                            end_at = snapshot.end_at,
                                            size = snapshot.size,
                                            status = snapshot.status,
                                            type = snapshot.type,
                                            error = snapshot.error)
    except Exception, e:
        LOG.error("Error register backup on DBMonitor %s" % (e))

def mysql_binlog_save(client, instance, cloudstack_hostattr):

    try:
        client.query('show master status')
        r = client.store_result()
        row = r.fetch_row(maxrows=0, how=1)
        binlog_file = row[0]['File']
        binlog_pos = row[0]['Position']

        client.query("show variables like 'datadir'")
        r = client.store_result()
        row = r.fetch_row(maxrows=0, how=1)
        datadir = row[0]['Value']

        output = {}
        command = 'echo "master=%s;position=%s" > %smysql_binlog_master_file_pos' % (binlog_file, binlog_pos, datadir)

        exit_status = exec_remote_command(server = instance.hostname.address,
                                        username = cloudstack_hostattr.vm_user,
                                        password = cloudstack_hostattr.vm_password,
                                        command = command,
                                        output = output)
    except Exception, e:
        LOG.error("Error saving mysql master binlog file and position: %s" % (e))


def make_instance_snapshot_backup(instance, error):

    LOG.info("Make instance backup for %s" % (instance))

    snapshot = Snapshot()
    snapshot.start_at = datetime.datetime.now()
    snapshot.type=Snapshot.SNAPSHOPT
    snapshot.status=Snapshot.RUNNING
    snapshot.instance = instance
    snapshot.environment = instance.databaseinfra.environment

    from dbaas_nfsaas.models import HostAttr as Nfsaas_HostAttr
    nfsaas_hostattr = Nfsaas_HostAttr.objects.get(host=instance.hostname)
    snapshot.export_path = nfsaas_hostattr.nfsaas_path

    databases = Database.objects.filter(databaseinfra=instance.databaseinfra)
    if databases:
        snapshot.database_name = databases[0].name

    snapshot.save()

    databaseinfra = instance.databaseinfra
    driver = databaseinfra.get_driver()
    client = driver.get_client(instance)
    cloudstack_hostattr = Cloudstack_HostAttr.objects.get(host=instance.hostname)

    try:
        LOG.debug('Locking instance %s' % str(instance))
        driver.lock_database(client)
        LOG.debug('Instance %s is locked' % str(instance))

        if type(driver).__name__ == 'MySQL':
            mysql_binlog_save(client, instance, cloudstack_hostattr)

        nfs_snapshot = NfsaasProvider.create_snapshot(environment = databaseinfra.environment,
                                                      plan = databaseinfra.plan,
                                                      host = instance.hostname)
        if 'error' in nfs_snapshot:
            errormsg = nfs_snapshot['error']
            error['errormsg'] = errormsg
            set_backup_error(databaseinfra, snapshot, errormsg)
            return False

        if 'id' in nfs_snapshot and 'snapshot' in nfs_snapshot:
            snapshot.snapshopt_id = nfs_snapshot['id']
            snapshot.snapshot_name = nfs_snapshot['snapshot']
        else:
            errormsg = 'There is no snapshot information'
            error['errormsg'] = errormsg
            set_backup_error(databaseinfra, snapshot, errormsg)
            return False

    except Exception, e:
        errormsg = "Error creating snapshot: %s" % (e)
        error['errormsg'] = errormsg
        set_backup_error(databaseinfra, snapshot, errormsg)
        return False

    finally:
        LOG.debug('Unlocking instance %s' % str(instance))
        driver.unlock_database(client)
        LOG.debug('Instance %s is unlocked' % str(instance))

    output = {}
    command = "du -sb /data/.snapshot/%s | awk '{print $1}'" % (snapshot.snapshot_name)
    try:
        exit_status = exec_remote_command(server = instance.hostname.address,
                                          username = cloudstack_hostattr.vm_user,
                                          password = cloudstack_hostattr.vm_password,
                                          command = command,
                                          output = output)
        size = int(output['stdout'][0])
        snapshot.size = size
    except Exception, e:
        snapshot.size = 0
        LOG.error("Error exec remote command %s" % (e))

    snapshot.status = Snapshot.SUCCESS
    snapshot.end_at = datetime.datetime.now()
    snapshot.save()
    register_backup_dbmonitor(databaseinfra, snapshot)

    return True


@app.task(bind=True)
@only_one(key="makedatabasebackupkey", timeout=1200)
def make_databases_backup(self):

    LOG.info("Making databases backups")
    worker_name = get_worker_name()
    task_history = TaskHistory.register(request=self.request,
        worker_name=worker_name, user=None)

    msgs = []
    status = TaskHistory.STATUS_SUCCESS
    databaseinfras = DatabaseInfra.objects.filter(plan__provider=Plan.CLOUDSTACK)
    error = {}
    for databaseinfra in databaseinfras:
        instances = Instance.objects.filter(databaseinfra=databaseinfra)
        for instance in instances:

            if not instance.databaseinfra.get_driver().check_instance_is_eligible_for_backup(instance):
                LOG.info('Instance %s is not eligible for backup' % (str(instance)))
                continue

            try:
                if make_instance_snapshot_backup(instance = instance, error = error):
                    msg = "Backup for %s was successful" % (str(instance))
                    LOG.info(msg)
                else:
                    status = TaskHistory.STATUS_ERROR
                    msg = "Backup for %s was unsuccessful. Error: %s" % (str(instance), error['errormsg'])
                    LOG.error(msg)
                print msg
            except Exception, e:
                status = TaskHistory.STATUS_ERROR
                msg = "Backup for %s was unsuccessful. Error: %s" % (str(instance), str(e))
                LOG.error(msg)

            msgs.append(msg)

    task_history.update_status_for(status, details="\n".join(msgs))

    return

def remove_snapshot_backup(snapshot):

    LOG.info("Removing backup for %s" % (snapshot))

    instance = snapshot.instance
    databaseinfra = instance.databaseinfra
    NfsaasProvider.remove_snapshot(environment = databaseinfra.environment,
                                   plan = databaseinfra.plan,
                                   host = instance.hostname,
                                   snapshopt = snapshot.snapshopt_id)

    snapshot.purge_at = datetime.datetime.now()
    snapshot.save()
    return


@app.task(bind=True)
@only_one(key="removedatabaseoldbackupkey", timeout=1200)
def remove_database_old_backups(self):

    worker_name = get_worker_name()
    task_history = TaskHistory.register(request=self.request,
        worker_name=worker_name, user=None)

    backup_retention_days = Configuration.get_by_name_as_int('backup_retention_days')

    LOG.info("Removing backups older than %s days" % (backup_retention_days))

    backup_time_dt = date.today() - timedelta(days=backup_retention_days)
    snapshots = Snapshot.objects.filter(start_at__lte=backup_time_dt, purge_at__isnull = True, instance__isnull = False, snapshopt_id__isnull = False)
    msgs = []
    status = TaskHistory.STATUS_SUCCESS
    if len(snapshots) == 0:
        msgs.append("There is no snapshot to purge")
    for snapshot in snapshots:
        try:
            remove_snapshot_backup(snapshot=snapshot)
            msg = "Backup %s removed" % (snapshot)
            LOG.info(msg)
        except:
            msg = "Error removing backup %s" % (snapshot)
            status = TaskHistory.STATUS_ERROR
            LOG.error(msg)
        msgs.append(msg)

    task_history.update_status_for(status, details="\n".join(msgs))

    return
