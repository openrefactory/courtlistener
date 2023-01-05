from django.contrib import admin

from cl.favorites.models import DocketTag, Favorite, UserTag
from cl.lib.pghistory_admin import EventHistoryAdmin


class FavoriteInline(admin.TabularInline):
    model = Favorite
    extra = 1
    raw_id_fields = (
        "user",
        "cluster_id",
        "audio_id",
        "docket_id",
        "recap_doc_id",
    )


@admin.register(Favorite)
class FavoriteAdmin(EventHistoryAdmin):
    list_display = (
        "id",
        "user",
        "cluster_id",
    )
    raw_id_fields = (
        "user",
        "cluster_id",
        "audio_id",
        "docket_id",
        "recap_doc_id",
    )


@admin.register(DocketTag)
class DocketTagAdmin(EventHistoryAdmin):
    raw_id_fields = ("docket", "tag")
    list_display = (
        "id",
        "tag",
    )


@admin.register(UserTag)
class UserTagAdmin(EventHistoryAdmin):
    raw_id_fields = ("user", "dockets")
    readonly_fields = ("date_modified", "date_created")


class UserTagInline(admin.StackedInline):
    model = UserTag
    extra = 0
