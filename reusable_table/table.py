from django.template import Template, Context
from django.core.paginator import Paginator, InvalidPage
from django.http import HttpResponse
from django.db.models.query import QuerySet, RawQuerySet
from django.utils.translation import ugettext

pagination_size_default = 25

formats = ["csv",]
try:
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph
    from reportlab.platypus import Table as PDFTable
    from reportlab.platypus import TableStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib import colors
    from reportlab.lib import pagesizes
    formats.append("pdf")
except ImportError:
    pass

from django.core.handlers.wsgi import WSGIRequest
from django.template import RequestContext

import os
import csv
import StringIO
import urllib

from tempfile import mkstemp
from datetime import datetime

path = os.path.join(os.path.dirname(__file__), "templates")


def paginate(queryset, number, size=pagination_size_default):
    try:
        number = int(number)
    except (ValueError, TypeError):
        number = 1
    pages = Paginator(queryset, size)
    if isinstance(queryset, RawQuerySet):
        pages._count = len(list(queryset))
    result = { "pages": pages, "count": pages.count, "jump": jump(pages, number) }
    if pages.num_pages > 1:
        result["paginated"] = True
    else:
        result["paginated"] = False
    try:
        result["page"] = pages.page(number)
    except InvalidPage:
        # no page, go to 1
        result["page"] = pages.page(1)
    return result


def jump(pages, index):
    res = { "start_ellipsis": False, "end_ellipsis": False }
    nums = pages.page_range
    side = 5
    index -= 1
    start = index - side
    if start > 0:
        res["start_ellipsis"] = True

    if start < 0:
        start = 0

    end = index + side + 1
    if end > (len(nums) + 1):
        res["pages_bit"] = nums[start:]
    else:
        res["pages_bit"] = nums[start:end]
        res["end_ellipsis"] = True

    return res

files = {}


def get_file(name):
    global files
    if name not in files:
        files[name] = open(os.path.join(path, name)).read()
    return files[name]


class Table:

    """Render models in a general paginated table.

    GET request arguments are used to carry ordering type, rendering,
    pagination.

    Each table has a name (an index into a global tables dictionary,)
    a model that the rows of the table represent, and fields.

    Fields consist of three parts: name, column, and bit.
    * name:  name of the column
    * column:  the value within the model to sort on (?)
    * bit:  the bit of html to render within the column
        -- the bit references the row's object via "{{object}}"
        -- remember to {% load i18n %} within the bit if you are using
           internationalization features
    """

    def __init__(self, model, fields, size, link_first = True):
        self.model = model
        results = []
        for head, column, bit in fields:
            results.append({"name": head, "column":column, "bit":bit})
        self.fields = results
        self.template_wrapper = Template(get_file("table_wrapper.html"))
        self.html_second_column = get_file("html_second_column.html")
        if link_first:
            self.html_first_column = get_file("html_first_column.html")
        else:
            self.html_first_column = get_file("html_second_column.html")
        self.pagination = Template(get_file("pagination.html"))
        self.size = size
        self.link_first = link_first

    def __call__(self, request, key, queryset, extra_context=None,
                 size=None, distinct=False, final_queryset=None):
        """Render the table.

        Works by identifying a handler function (handle_csv,
        handle_html, ...) for the request,  and passing the queryset,
        which this function may construct.

        Returns (format-of-rendering, handler-call-result)

        Note that:  We here at Pokemon have hacked around this a few
        times.  For example, we wanted to send in a list of
        pre-made dictionaries, rather than QuerySets...  So, how did we
        stuff them in?  That's why final_queryset was made.

        request  -- the request to render the Table for
        key  -- the key identifies the request GET's format_# to use to find the format
                ("csv", "html", "pdf", ...) for the table rendering
        queryset  -- either a QuerySet instance, or
                     (50% confident?) a Q() used to identify self.model instances
                                      by filtering on the model.
        extra_context  -- (forwarded to handle_X function)
        size  -- ("page" of a table size;  forwarded to handle_X function)
        distinct  -- if True, performs the SQL Query with DISTINCT
        final_queryset  -- if supplied, circumvent the normal QuerySet
                           detection or discovery, and force use of
                           THIS data, which may well be a list of
                           dictionaries...

            Note that: If a queryset of type QuerySet is supplied,
                       it is used as is, unless final_queryset overrides.

        The format identified is used to call handle_csv, handle_html,
        or handle_pdf.

        key  -- involved (somehow) in identifying the format to use:
                csv, html,pdf , ...
        queryset  -- the queryset used to identify self.model instances
        """
        self.key = key
        if not size:
            size = self.size
        format = request.GET.get("format_%s" % self.key, "html")
        method = getattr(self, "handle_%s" % format, None)
        if final_queryset:
            queryset = final_queryset
        else:
            if not isinstance(queryset, QuerySet):
                queryset = self.model.objects.filter(queryset)
                if distinct:
                    queryset = queryset.distinct()
        if method:
            return format, method(request, queryset, extra_context, size)
        else:
            raise NotImplementedError("The format: %s is not handled" % format)

    def handle_csv(self, request, queryset, extra_context=None, size=None):
        output = StringIO.StringIO()
        csvio = csv.writer(output)
        header = False
        for row in queryset:
            ctx = Context({"object": row })
            if extra_context:
                ctx.update(extra_context)
            if not header:
                csvio.writerow([ugettext(f["name"]) for f in self.fields])
                header = True
            values = [ Template(h["bit"]).render(ctx) for h in self.fields ]
            csvio.writerow(values)

        response = HttpResponse(mimetype='text/csv')
        response['Content-Disposition'] = 'attachment; filename=report.csv'
        response.write(output.getvalue())
        return response

    def handle_pdf(self, request, queryset, extra_context=None, size=None):
        if "pdf" not in formats:
            raise ImportError("The site is not configured to handle pdf.")

        # this is again some quick and dirty sample code
        elements = []
        styles = getSampleStyleSheet()
        styles['Title'].alignment = TA_LEFT
        styles['Title'].fontName = styles['Heading2'].fontName = "Helvetica"
        styles["Normal"].fontName = "Helvetica"
        filename = mkstemp(".pdf")[-1]
        doc = SimpleDocTemplate(filename)
        doc.pagesize = pagesizes.landscape(pagesizes.LETTER)

        request = WSGIRequest({'REQUEST_METHOD':'GET'})
        site = RequestContext(request).get('site')
        if site and site.get('title'):
            elements.append(Paragraph(site.get('title'), styles['Title']))

        elements.append(Paragraph("%s List" % self.model.__name__, styles['Heading2']))

        data = []
        header = False
        for row in queryset:
            if not header:
                data.append([f["name"] for f in self.fields])
                header = True
            ctx = Context({"object": row })
            if extra_context:
                ctx.update(extra_context)
            values = [ Template(h["bit"]).render(ctx) for h in self.fields ]
            data.append(values)

        table = PDFTable(data)
        table.setStyle(TableStyle([
            ('ALIGNMENT', (0,0), (-1,-1), 'LEFT'),
            ('LINEBELOW', (0,0), (-1,-0), 2, colors.black),
            ('LINEBELOW', (0,1), (-1,-1), 0.8, colors.lightgrey),
            ('FONT', (0,0), (-1, -1), "Helvetica"),
            ('ROWBACKGROUNDS', (0,0), (-1, -1), [colors.whitesmoke, colors.white]),
        ]))
        elements.append(table)
        elements.append(Paragraph("Created: %s" % datetime.now().strftime("%d/%m/%Y"), styles["Normal"]))
        doc.build(elements)

        response = HttpResponse(mimetype='application/pdf')
        response['Content-Disposition'] = 'attachment; filename=report.pdf'
        response.write(open(filename).read())
        os.remove(filename)
        return response

    def handle_html(self, request, queryset, extra_context=None, size=None):
        """Render an HTML Table.

        Returns the HTML string for the table.

        request  -- the request, used to find the page# for this key,
                    and to identify "user" (the user object) when rendering
        queryset  -- the queryset of object instances, 1 per table row
        extra_context  -- additions/overrides to the context used when
                          rendering table cells
        size  -- the number of rows to display on this page

        The default context is:
          {"object": row,
           "counter": k,  -- ?  (guess: starting from 1,
                                        the row number in the table)
           "total_counter",  -- ?  (guess: starting from 1 on page 1,
                                           the row number in the table)
           "user": request.user}

        The template for a given cell comes from the "bit", position #2
        in the reusable_table fields.
        """
        # get the default page number
        default = request.GET.get("page_%s" % self.key, 1)
        try:
            default = int(default)
        except (TypeError, ValueError):
            default = 1

        sort_key, sort_value = None, None
        for h in self.fields:
            column = h["column"]
            tmp_sort_key = "sort_%s_%s" % (self.key, column)
            value = request.GET.get(tmp_sort_key, None)
            h["asc"] = True
            sort_value = "asc"
            if value == "asc":
                queryset = queryset.order_by(column)
                sort_key = tmp_sort_key
                break
            elif value == "desc":
                queryset = queryset.order_by("-%s" % column)
                sort_key = tmp_sort_key
                h["asc"] = False
                sort_value = "desc"
                break

        paginated = paginate(queryset, default, size)
        rows = []
        k = 1
        for row in paginated["page"].object_list:
            ctx = Context({"object": row,
                           "counter": k,
                           "total_counter": paginated["page"].start_index() + k - 1,
                           "user": request.user})
            if extra_context:
                ctx.update(extra_context)

            build = []
            first = hasattr(row, "get_absolute_url") and self.link_first
            for h in self.fields:
                if first:
                    bit = self.html_first_column % (
                        row.get_absolute_url(),
                        Template(h["bit"]).render(ctx),
                    )
                    first = False
                else:
                    bit = self.html_second_column % (
                        Template(h["bit"]).render(ctx)
                    )
                build.append(bit)

            rows.append("".join(build))
            k += 1

        results = {}
        for key in request.GET.keys():
            if key.startswith("page_") or key == sort_key:
                continue
            results[key] = [ value.encode("utf-8") for value in request.GET.getlist(key) ]

        keys = urllib.urlencode(results, True)
        self.context = {
            "columns": self.fields,
            "rows": rows,
            "object_list": paginated,
            "table_key": self.key,
            "formats": formats,
            "sort_key": sort_key,
            "sort_value": sort_value,
            "filtered_query": keys
        }
        return (self.template_wrapper.render(Context(self.context)) +
                self.pagination.render(Context(self.context)))

tables = {}


def register(name, model, fields, size=pagination_size_default, link_first=True):
    global tables
    for field in fields: assert len(field) == 3
    tables[name] = Table(model, fields, size, link_first)


def get(request, tabs, extra_context=None, size=None, distinct=False):
    """Returns results of rendering named tables w/ associated queries.

    Returns (nonhtml, result) where:
    nonhtml  -- the first non-HTML result object, from iterating tabs
    result  -- list of results, whether HTML string, or non-HTML result

    Arguments:
    request  -- general Django request
    tabs  -- list of 2-tuples;  [(name-of-reusable-table, Q-instance),
                                  "name",                 "query"  ...]
    extra_context  -- extra context for rendering table cells;
                      forwarded to Table.__call__
    size  -- page size; forwarded to Table.__call__
    distinct  -- apply DISTINCT to query; forwarded to Table.__call__

    NOTE: This function doesn't use tables[X](..., final_queryset=Y),
          so you can NOT force a queryset through via this function.
    """
    result = []
    nonhtml = None
    x = 1
    for name, query in tabs:
        # format: "html", "csv", or "pdf"
        # tab: the resulting HTML string (or result of handle_Xformat(...))
        format, tab = tables[name](request, str(x), query, size=size,
                                   extra_context=extra_context,
                                   distinct=distinct)
        if not nonhtml and format != "html":
            nonhtml = tab
        result.append(tab)
        x += 1
    return nonhtml, result


def get_with_final_qs(request, tabs, extra_context=None, size=None, distinct=False, final_queryset=None):
    result = []
    nonhtml = None
    x = 1
    for name, query in tabs:
        format, tab = tables[name](request, str(x), query, size=size,
                                   extra_context=extra_context,
                                   distinct=distinct,
                                   final_queryset=final_queryset)
        if not nonhtml and format != "html":
            nonhtml = tab
        result.append(tab)
        x += 1

    return nonhtml, result


def get_dict(request, tabs, extra_context=None, size=None, distinct=False):
    result = {}
    nonhtml = None
    x = 1
    for name, query in tabs:
        format, tab = tables[name](request, str(x), query, size=size,
                                   extra_context=extra_context,
                                   distinct=distinct)
        if not nonhtml and format != "html":
            nonhtml = tab
        result[name] = tab
        x += 1

    return nonhtml, result
