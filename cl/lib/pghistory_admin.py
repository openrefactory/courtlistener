import pghistory.models
from django import http
from django.apps import apps as django_apps
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.contrib.admin.utils import unquote
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import re_path, resolve, reverse
from django.utils.encoding import force_str
from django.utils.html import mark_safe
from django.utils.text import capfirst
from django.utils.translation import gettext as _


class EventHistoryAdmin(admin.ModelAdmin):
    object_history_template = "pghistory/object_history.html"
    object_history_form_template = "pghistory/object_history_form.html"
    related_name_event = "event"
    order_by_history = "-pgh_id"

    # def __init__(self):
    #     super(EventHistoryAdmin, self).__init__()

    def __init__(self, *args, **kwargs):
        super(EventHistoryAdmin, self).__init__(*args, **kwargs)

    def _related_name_not_exist_redirect(self, request, opts, object_id):
        """Create a message informing the user that the object doesn't exist
        and return a redirect to the admin index page.
        """
        msg = _(
            "%(name)s with related name doesn’t exist. Set correct "
            "related_name_event in your model admin definition."
        ) % {
            "name": opts.verbose_name,
        }
        self.message_user(request, msg, messages.WARNING)
        url = reverse("admin:index", current_app=self.admin_site.name)
        return HttpResponseRedirect(url)

    def history_view(self, request, object_id, extra_context=None):
        """The event history admin view for this model."""

        model = self.model  # Original model
        obj = self.get_object(request, unquote(object_id))  # Original object

        if obj is None:
            # Object doesn't exist
            return self._get_obj_does_not_exist_redirect(
                request, model._meta, object_id
            )

        if not self.has_view_or_change_permission(request, obj):
            # User don't have permission to change object
            raise PermissionDenied

        if not hasattr(obj, self.related_name_event):
            # Model doesn't have the related name for events or is not
            # specified
            return self._related_name_not_exist_redirect(request, model._meta)

        # Order all the events from object
        action_list = getattr(obj, self.related_name_event).order_by(
            self.order_by_history
        )

        # Store meta from model
        opts = model._meta

        context = {
            **self.admin_site.each_context(request),
            "title": _("Change history: %s") % obj,
            "subtitle": None,
            "action_list": action_list,
            "module_name": str(capfirst(opts.verbose_name_plural)),
            "object": obj,
            "opts": opts,
            "preserved_filters": self.get_preserved_filters(request),
            **(extra_context or {}),
        }

        request.current_app = self.admin_site.name

        return self.render_history_view(
            request, self.object_history_template, context
        )

    def render_history_view(self, request, template, context, **kwargs):
        """Catch call to render, to allow overriding."""
        return render(request, template, context, **kwargs)

    def get_urls(self):
        """Add additional urls to revert objects"""
        urls = super().get_urls()
        admin_site = self.admin_site
        opts = self.model._meta
        info = opts.app_label, opts.model_name
        history_urls = [
            re_path(
                "^([^/]+)/history/([^/]+)/$",
                admin_site.admin_view(self.history_form_view),
                name="%s_%s_event_history" % info,
            )
        ]
        return history_urls + urls

    def response_change(self, request, obj):
        """Add message to indicate user if revert was successful"""
        verbose_name = obj._meta.verbose_name

        msg = _('The %(name)s "%(obj)s" was reverted successfully.') % {
            "name": force_str(verbose_name),
            "obj": force_str(obj),
        }
        self.message_user(request, f"{msg}")

        opts = self.model._meta
        info = opts.app_label, opts.model_name
        redirect_url = "admin:%s_%s_history" % info

        return http.HttpResponseRedirect(
            reverse(redirect_url, kwargs={"object_id": obj.pk})
        )

    def response_change_failed(self, request, obj):
        """Add message to indicate user if revert wasn't successful"""
        verbose_name = obj._meta.verbose_name

        msg = _(
            'RuntimeError: The %(name)s "%(obj)s" can\'t be reverted. '
            "Maybe some fields were excluded for tracking."
        ) % {
            "name": force_str(verbose_name),
            "obj": force_str(obj),
        }
        self.message_user(request, f"{msg}", level=messages.ERROR)

        opts = self.model._meta
        info = opts.app_label, opts.model_name
        redirect_url = "admin:%s_%s_history" % info

        return http.HttpResponseRedirect(
            reverse(redirect_url, kwargs={"object_id": obj.pk})
        )

    def get_readonly_fields(self, request, obj=None):
        """Make all fields readonly"""

        resolve_url = resolve(path=request.path)
        if "_event_history" in resolve_url.url_name:
            # This is revert object details view, make fields readonly

            readonly_fields = list(
                set(
                    [field.name for field in self.opts.local_fields]
                    + [field.name for field in self.opts.local_many_to_many]
                )
            )

            if "is_submitted" in readonly_fields:
                readonly_fields.remove("is_submitted")

            return readonly_fields
        else:
            # This is edit object view, call super for usual behavior
            return super(EventHistoryAdmin, self).get_readonly_fields(
                request, obj
            )

    def history_form_view(
        self, request, object_id, version_id, extra_context=None
    ):
        """View to display form with object event data, you can revert it here"""

        request.current_app = self.admin_site.name
        original_opts = self.model._meta
        original_model = self.model
        model = self.model._meta.get_field(
            self.related_name_event
        ).related_model

        # Get original object
        original_obj = get_object_or_404(
            original_model, **{original_opts.pk.attname: object_id}
        )

        # Get event object using version_id
        revert_obj = get_object_or_404(
            model,
            **{original_opts.pk.attname: object_id, "pgh_id": version_id},
        )
        revert_obj._state.adding = False

        if not self.has_change_permission(request, revert_obj):
            # You can't revert objects
            raise PermissionDenied

        formsets = []
        form_class = self.get_form(request, revert_obj)
        if request.method == "POST":
            # Call revert method
            try:
                revert_obj.revert()
            except RuntimeError:
                # Object can't be reverted, maybe some fields were excluded
                return self.response_change_failed(request, original_obj)
            return self.response_change(request, original_obj)

        else:
            # Display form with object data, this can't be edited
            form = form_class(instance=revert_obj)

        # Generate full admin form
        admin_form = helpers.AdminForm(
            form,
            self.get_fieldsets(request, revert_obj),
            self.prepopulated_fields,
            self.get_readonly_fields(request, revert_obj),
            model_admin=self,
        )

        pghistory_events_last_change = (
            pghistory.models.Events.objects.filter(
                pgh_obj_model=f"{original_opts.app_label}.{original_opts.object_name}",
                pgh_obj_id=original_obj.pk,
                pgh_id=revert_obj.pk,
            )
            .order_by("-pgh_created_at")
            .first()
        )

        last_diff = None

        if pghistory_events_last_change:
            last_diff = pghistory_events_last_change.pgh_diff

        model_name = original_opts.model_name  # Original model name

        url_triplet = self.admin_site.name, original_opts.app_label, model_name
        changelist_url_name = "%s:%s_%s_changelist" % url_triplet
        change_url_name = "%s:%s_%s_change" % url_triplet
        history_url_name = "%s:%s_%s_history" % url_triplet
        context = {
            "title": _("Revert %s") % force_str(revert_obj),
            "adminform": admin_form,
            "object_id": object_id,
            "original": original_obj,
            "is_popup": False,
            "media": mark_safe(self.media + admin_form.media),
            "errors": helpers.AdminErrorList(form, formsets),
            "app_label": original_opts.app_label,
            "original_opts": original_opts,
            "changelist_url": changelist_url_name,
            "change_url": change_url_name,
            "history_url": history_url_name,
            # Context variables copied from render_change_form
            "add": False,
            "change": True,
            "has_add_permission": self.has_add_permission(request),
            # Permission on original object, to avoid add extra permissions for
            # generated event table
            "has_change_permission": self.has_change_permission(
                request, original_obj
            ),
            "has_delete_permission": self.has_delete_permission(
                request, original_obj
            ),
            "has_file_field": True,
            "has_absolute_url": False,
            "form_url": "",
            "opts": model._meta,
            "content_type_id": self.content_type_model_cls.objects.get_for_model(
                self.model
            ).id,
            "save_as": self.save_as,
            "save_on_top": self.save_on_top,
            "root_path": getattr(self.admin_site, "root_path", None),
            "last_diff": last_diff,
            "revert_obj": revert_obj,
        }
        context.update(self.admin_site.each_context(request))
        context.update(extra_context or {})
        extra_kwargs = {}
        return self.render_history_view(
            request, self.object_history_form_template, context, **extra_kwargs
        )

    @property
    def content_type_model_cls(self):
        """Returns the ContentType model class."""
        return django_apps.get_model("contenttypes.contenttype")
