# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals
import logging
from django.utils.translation import ugettext_lazy as _
from django import forms
from .. import models

log = logging.getLogger(__name__)

class PlanForm(forms.ModelForm):
    
    class Meta:
        model = models.Plan


    def clean(self):
        """Validates the form to make sure that there is at least one default plan"""

        cleaned_data = super(PlanForm, self).clean()
        is_default = cleaned_data.get("is_default")
        engine_type = cleaned_data.get("engine_type")

        if not is_default:
            plans = models.Plan.objects.filter(is_default=True, engine_type=engine_type)
            if not plans:
                msg = _("At least one plan must be default")
                log.warning(u"%s" % msg)
                raise forms.ValidationError(msg)

        return cleaned_data

class PlanAttributeInlineFormset(forms.models.BaseInlineFormSet):

    def clean(self):
        # get forms that actually have valid data
        count = 0
        for form in self.forms:
            try:
                if form.cleaned_data:
                    count += 1
            except AttributeError:
                # annoyingly, if a subform is invalid Django explicity raises
                # an AttributeError for cleaned_data
                pass
        if count < 1:
            log.warning(u"%s" % _("You must have at least one plan attribute"))
            raise forms.ValidationError(_("You must have at least one plan attribute"))
        