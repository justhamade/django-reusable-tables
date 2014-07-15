from django import template

register = template.Library()

def pagination(object_list):
    return {"object_list":object_list}
    
register.inclusion_tag('pagination.html')(pagination)