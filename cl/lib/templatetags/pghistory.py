from django import template

register = template.Library()


def getattribute(value, arg):
    """Gets an attribute of an object dynamically from a string name"""

    return getattr(value, arg, None)


register.filter("getattribute", getattribute)


@register.inclusion_tag("pghistory/_object_history_list.html",
                        takes_context=True)
def display_list(context):
    return context
