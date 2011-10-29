# This software and any associated files are copyright 2010 Brian Carver and
# Michael Lissner.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from alert.alerts.forms import CreateAlertForm
from alert.search.forms import SearchForm
from alert.search.models import Document
from alert.userHandling.models import UserProfile

from django.contrib import messages
from django.core.paginator import Paginator, InvalidPage, EmptyPage
from django.shortcuts import render_to_response
from django.shortcuts import HttpResponseRedirect
from django.template import RequestContext
from django.utils.text import get_text_list

import re


def preparseQuery(query):
    query = query.lower()

    # @doctext needs to become @(doctext,dochtml).
    # @(doctext) needs to become @(doctext, dochtml).
    # @(doctext,court) needs to be come @(doctext,dochtml,court)
    query = re.sub('doctext', 'doctext,dochtml', query)
    # @doctext now is @doctext,dochtml --> BAD
    # @(doctext) is now @(doctext,dochtml) --> GOOD
    # @(doctext,court) is now @(doctext,dochtml,court) --> GOOD
    query = re.sub('@doctext,dochtml', '@(doctext,dochtml)', query)
    # All are now good.

    return query

def adjustQueryForUser(query):
    """This is where the "Did you mean" type of thing lives, for example,
    where we correct the user's input if needed.

    Currently, though, it's not implemented.
    """
    return query


def messageUser(query, request):
    # before searching, check that all fieldnames are valid. Create message if not.
    # Alter the query string if needed so that it will return the correct results.
    # this thread has a solution using pyparsing which is probably a better approach to investigate:
    # http://stackoverflow.com/questions/2677713/regex-for-finding-valid-sphinx-fields
    # for testing: @court @casename foo,bar @(doctext, courthouse, docstatus) @(docstatus, casename) @(casename) @courtname (court | doctext)
    # this catches simple fields such as @court, @field and puts them in a list
    attributes = re.findall('(?:@)([^\( ]*)', query)

    # this catches more complicated ones, like @(court), and @(court, test)
    regex0 = re.compile('''
        @               # at sign
        (?:             # start non-capturing group
            \w+             # non-whitespace, one or more
            \b              # a boundary character (i.e. no more \w)
            |               # OR
            (               # capturing group
                \(              # left paren
                [^@(),]+        # not an @(),
                (?:                 # another non-caputing group
                    , *             # a comma, then some spaces
                    [^@(),]+        # not @(),
                )*              # some quantity of this non-capturing group
                \)              # a right paren
            )               # end of non-capuring group
        )           # end of non-capturing group
        ''', re.VERBOSE)


    messageText = ""
    # and this puts them into the attributes list.
    groupedAttributes = re.findall(regex0, query)
    for item in groupedAttributes:
        attributes.extend(item.strip("(").strip(")").strip().split(","))

    # check if the values are valid.
    validRegex = re.compile(r'^\W?court\W?$|^\W?casename\W?$|^\W?westcite\W?$|^\W?docketnumber\W?$|^\W?docstatus\W?$|^\W?doctext\W?$')

    # if they aren't add them to a new list.
    badAttrs = []
    for attribute in attributes:
        if len(attribute) == 0:
            # if it's a zero length attribute, we punt
            continue
        if validRegex.search(attribute.lower()) == None:
            # if the attribute from the search isn't in the valid list
            if attribute not in badAttrs:
                # and the attribute isn't already in the list
                badAttrs.append(attribute)
        if " " in attribute:
            # if there is a space in the item
            if "Multiple" not in messageText:
                # we only add this warning once.
                messageText += "Mutiple field searches cannot contain spaces.<br>"


    # pluralization is a pain, but we must do it...
    if len(badAttrs) == 1:
        messageText += '<strong>' + get_text_list(badAttrs, "and") + '</strong> is not a \
        valid field. Valid fields are @court, @caseName, @westCite, @docketNumber,\
        @docStatus and @docText.'
    elif len(badAttrs) > 1:
        messageText += '<strong>' + get_text_list(badAttrs, "and") + '</strong> are not \
        valid fields. Valid fields are @court, @caseName, @westCite, @docketNumber,\
        @docStatus and @docText.'

    if len(messageText) > 0:
        messages.add_message(request, messages.INFO, messageText)

    return True


def getDateFiledOrReturnZero(doc):
    """Used for sorting dates. Returns the date field or the earliest date
    possible in Python. With this done, items without dates will be listed
    last without throwing errors to the sort function."""
    if (doc.dateFiled != None):
        return doc.dateFiled
    else:
        import datetime
        return datetime.date(1, 1, 1)


def showResults(request):
    '''Show the results for a query'''

    try:
        query = request.GET['q']
    except:
        # if somebody is URL hacking at /search/results/
        query = ""

    # this handles the alert creation form.
    if request.method == 'POST':
        from alert.userHandling.models import Alert
        # an alert has been created
        alertForm = CreateAlertForm(request.POST)
        if alertForm.is_valid():
            cd = alertForm.cleaned_data

            # save the alert
            a = CreateAlertForm(cd)
            alert = a.save() # this method saves it and returns it

            # associate the user with the alert
            up = request.user.get_profile()
            up.alert.add(alert)
            messages.add_message(request, messages.SUCCESS,
                'Your alert was created successfully.')

            # and redirect to the alerts page
            return HttpResponseRedirect('/profile/alerts/')
    else:
        # the form is loading for the first time, load it, then load the rest
        # of the page!
        alertForm = CreateAlertForm(initial={'alertText': query, 'alertFrequency': "dly"})

    # alert the user if there are any errors in their query
    messageUser(query, request)

    # adjust the query if need be for the search to happen correctly.
    query = adjustQueryForUser(query)
    internalQuery = preparseQuery(query)

    # NEW SEARCH METHOD
    try:
        queryset = Document.search.query(internalQuery)
        results = queryset.set_options(mode="SPH_MATCH_EXTENDED2").order_by('-dateFiled')
    except:
        results = []

    # Put the results in order by dateFiled. Fixes issue 124
    # From: http://wiki.python.org/moin/HowTo/Sorting/
    # Need to do the [0:results.count()] business, else returns only first 20.
    # results = sorted(results[0:results.count()], key=getDateFiledOrReturnZero, reverse=True)

    # next, we paginate we will show ten results/page
    paginator = Paginator(results, 10)

    # this will fail when the search fails, so try/except is needed.
    try:
        numResults = paginator.count
    except:
        numResults = 0

    # Make sure page request is an int. If not, deliver first page.
    try:
        page = int(request.GET.get('page', '1'))
    except ValueError:
        page = 1

    # only allow queries up to page 100.
    if page > 100:
        return render_to_response('search/results.html', {'over_limit': True,
            'query': query, 'alertForm': alertForm},
            RequestContext(request))

    # If page request is out of range, deliver last page of results.
    try:
        results = paginator.page(page)
    except (EmptyPage, InvalidPage):
        results = paginator.page(paginator.num_pages)
    except:
        results = []

    return render_to_response('search/results.html', {'results': results,
        'numResults': numResults, 'query': query, 'alertForm': alertForm},
        RequestContext(request))


def toolsPage(request):
    return render_to_response('tools.html', {}, RequestContext(request))
