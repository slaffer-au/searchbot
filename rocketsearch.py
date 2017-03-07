#!/usr/bin/env python

"""
Usage:
    rocketsearch.py [options]

Options:
    -h, --help                                     Show this help message and exit.
    -r, --refresh-cache                            Refresh cached user and org data from Zendesk
    -l LEVEL, --level LEVEL                        Logging level during execution. Available options: DEBUG, INFO, WARNING, ERROR (default), CRITICAL [default: WARNING]
    -c CONFIGFILE, --config CONFIGFILE             Provide a file containing credentials and settings [default: ./rocketsearch.yml]
    --list-channels                                Do not start the Slack bot, instead return a list of the current channels. Used to determine channel id for configuration.
"""

import requests, re, yaml, os, pickle
from docopt import docopt
from urllib import urlencode
from jira import JIRA, JIRAError
from slackclient import SlackClient
from time import sleep
from simple_salesforce import Salesforce

def getZDOutput(credentials, subdomain, r_type, **kwargs):
    # Use Zendesk Query API to search
    session = requests.Session()
    session.auth = credentials
    print r_type
    print kwargs, type(kwargs)

    if kwargs and kwargs["params"]:
        url = 'https://%s.zendesk.com/api/v2/%s.json?%s' % (subdomain, r_type, urlencode(kwargs["params"]))
    else:
        url = 'https://' + zd_domain + '.zendesk.com/api/v2/' + r_type + '.json'

    data = []
    while url:
        print url
        response = session.get(url, timeout=10)
        if not response:
            print "response timed out???"
        if response.status_code != 200:
            print('Status:', response.status_code, 'Problem with the request. Exiting.')
            return None
        # Get all responses as JSON and convert to dict
        t_data = response.json()
        if r_type == "search":
            data.extend(t_data['results'])
        else:
            data.extend(t_data[r_type])
        url = t_data['next_page']
    print data
    return data

def parseZDOutput(data):
    # Search for tickets in Zendesk query results
    tickets = []
    others = []
    for result in data:
        if result['result_type'] == "ticket":
            tickets.append(result)
        else:
            others.append(result)
    return tickets

def printZDData(tickets):
    # Function to print all Zendesk results and format them for console

    # Fields I care about
    t_fields = ['id', 'subject', 'submitter_id','assignee_id', 'status', 'description']

    for ticket in tickets:
        for field in t_fields:
            if field == "description":
                print field.capitalize(), ": ", ticket[field][:100].replace('\n', ' ')
            else:
                print field.capitalize(), ": ", ticket[field]
        print ""

def respondZDData(tickets, result_limit):
    # Function to return all Zendesk results, formatted as a single string for Slack

    # Fields I care about
    t_fields = ['id', 'subject', 'submitter_id','assignee_id', 'organization_id', 'status', 'description']
    response = ""
    result = 0

    for ticket in tickets:
        if result < result_limit:
            result += 1
            for field in t_fields:
                if field == "id":
                    human_url = "https://cumulusnetworks.zendesk.com/agent/tickets/"+str(ticket[field])
                    response = response+"*ID*: <"+human_url+"|#"+str(ticket[field])+">\n"
                elif field == "submitter_id":
                    try:
                        response = response + "*Submitter*: %s (%s)\n" % (str(zd_users[ticket[field]]["name"]), \
                                                                          str(zd_users[ticket[field]]["email"]))

                    except KeyError:
                        response = response + "*Submitter*: " + str(ticket[field]) + "\n"
                elif field == "assignee_id":
                    try:
                        response = response + "*Assignee*: " + str(zd_users[ticket[field]]["name"]) + "\n"
                    except KeyError:
                        response = response + "*Assignee*: " + str(ticket[field]) + "\n"
                elif field == "organization_id":
                    try:
                        response = response + "*Organisation*: " + str(zd_orgs[ticket[field]]["name"]) + "\n"
                    except KeyError:
                        response = response + "*Organisation*: " + str(ticket[field]) + "\n"
                elif field == "description":
                    response = response + "*Description*: " + ticket[field]\
                        [:100].replace('\n', ' ').replace("\r", "") + "\n"
                else:
                    response = response + "*"+str(field.capitalize())+"*" + ": " + str(ticket[field]) + "\n"
            response = response + "\n"
    return response

def connectToJira(options):
    # Use JIRA API to establish an authenticated session.
    jira = JIRA(server=options['server'], basic_auth=(options['username'], options['password']))
    return jira

def getJiraTickets(jira, search_str, text_only):
    # Uses JIRA API to search for tickets matching the JQL language query string. Returns a list of JIRA Issue objects.
    if text_only:
        # Searches based on text only. Shortcuts full JQL.
        tickets = jira.search_issues("text ~ '%s'" % search_str)
    else:
        # Must be a full JQL query.
        tickets = jira.search_issues(search_str)
    return tickets

class jira_bug:
    # Takes an JIRA ticket ID and generates a dict of fields
    def __init__(self, id):
        self.id = id
        self.jiraObj = jira.issue(self.id)
        self.fields = vars(self.jiraObj.fields)

    def printBugDetails(self):
        # Function to print all JIRA field results and format them for console
        jr_fields = ['summary', 'status', 'reporter', 'assignee', 'customfield_10602',
                     'description']
        print self.fields
        print "ID : ", self.id
        for field in jr_fields:
            try:
                if field == "customfield_10602":
                    self.sprint = re.search(r'Release\s\d.\d.\d+', str(self.fields[field])).group()
                    print "Sprint : %s" % self.sprint
                elif field == "description":
                    print  "%s : %s" % (field.capitalize(), self.fields[field][:100])
                else:
                    print  "%s : %s" % (field.capitalize(), self.fields[field])
            except (AttributeError, TypeError, KeyError) as e:
                if KeyError:
                    print "No key %s for bug %s." % (field, self.id)
                elif (AttributeError or TypeError) and "NoneType" in str(e):
                    print str(e)
                else:
                    raise

        print("\n")

    def respondBugDetails(self):
        # Function to return all JIRA fields, formatted as a single string for Slack
        jr_fields = ['summary', 'status', 'reporter', 'assignee', 'customfield_10602',
                     'description']
        response = "*ID*: <https://tickets.cumulusnetworks.com/browse/%s|%s>\n" % (self.id, self.id)
        for field in jr_fields:
            try:
                if field == "customfield_10602":
                    self.sprint = re.search(r'Release\s\d.\d.\d+', str(self.fields[field])).group()
                    response += "*Sprint*: %s\n" % self.sprint
                elif field == "description":
                    response += "*%s*: %s\n" % (field.capitalize(),
                                                self.fields[field][:100].replace('\n', ' ').replace("\r", ""))
                else:
                    response = response + "*%s*: %s\n" % (field.capitalize(), self.fields[field])
            except (AttributeError, TypeError, KeyError) as e:
                if ("NoneType" in str(e)) or KeyError:
                    # Hit here if field does not exist or value None
                    pass
                else:
                    raise

        response = response + "\n"
        return response

class slack:
    # Creates objects for incoming messages
    def __init__(self, message):
        self.message = message
        self.text = self.message["text"]
        self.channel = self.message["channel"]
        self.user = self.message["user"]
        # Check for ourselves so we don't respond to our own messages
        self.isBot = False
        if slackBot == str(self.user):
            self.isBot = True

    def getChannelType(self):
        # Determine what type of channel the message came from
        self.isDM = False
        self.isPrivate = False
        self.isPublic = False

        if re.match(r'D', str(self.channel)):
            print "DM True"
            self.isDM = True
        elif re.match(r'G', str(self.channel)):
            print "Private True"
            self.isPrivate = True
        elif re.match(r'C', str(self.channel)):
            print "Public True"
            self.isPublic = True
        else:
            print "Unknown channel type"

    def checkInvoked(self):
        # Function to see if the bot was "invoked"
        # That changes based on channel type
        self.getChannelType()
        print "made it past channel selection"
        # Once we have the channel type, use regex to see if the bot was "invoked"
        self.search = search(self)
        if self.search.invoked:
            return True

    def response(self, string):
        # Pushes the bot's response to Slack postMessage API
        print "Response to channel %s is: \n%s" % (self.channel, string)
        rocketsearch.api_call("chat.postMessage", channel=self.channel, text=string, as_user=True, unfurl_links=False)

class search:
    # Determines and assigns search parameters to the Slack messages. Determine whether bot was invoked.
    def __init__(self, slackObj):
        self.invoked = True
        self.zd = False
        self.jira = False
        self.sfdc = False
        self.textonly = False
        self.help = False

        # If it's a direct message to the bot
        if slackObj.isDM and re.search(r'zendesk', slackObj.text, re.I):
            print "DM zendesk: " + slackObj.text
            self.zd = True
        elif slackObj.isDM and re.search(r'jira', slackObj.text, re.I):
            print "DM jira: " + slackObj.text
            self.jira = True
        elif slackObj.isDM and re.search(r'sf|salesforce', slackObj.text, re.I):
            print "DM sfdc: " + slackObj.text
            self.sfdc = True
        elif slackObj.isDM and re.search(r'text', slackObj.text, re.I):
            print "DM text: " + slackObj.text
            self.textonly = True
            self.zd = True
            self.jira = True
        elif slackObj.isDM and re.search(r'help', slackObj.text, re.I):
            print "DM help: " + slackObj.text
            self.help = True

        # If we're tagged in a channel with "@<BOT>" at the start of a line
        elif re.search(r'^(<@%s.*?zendesk)' % slackBot, slackObj.text, re.I):
            print "Channel zendesk: " + slackObj.text
            self.zd = True
        elif re.search(r'^(<@%s.*?jira)' % slackBot, slackObj.text, re.I):
            print "Channel jira: " + slackObj.text
            self.jira = True
        elif re.search(r'^(<@%s.*? sf|salesforce)' % slackBot, slackObj.text, re.I):
            print "Channel sfdc: " + slackObj.text
            self.sfdc = True
        elif re.search(r'^(<@%s.*?text)' % slackBot, slackObj.text, re.I):
            print "Channel text: " + slackObj.text
            self.textonly = True
            self.zd = True
            self.jira = True
        elif re.search(r'^(<@%s.*?help)' % slackBot, slackObj.text, re.I):
            print "Channel help: " + slackObj.text
            self.help = True
        else:
            self.invoked = False
            print "I was not invoked or source was selected"

    def getSearchParams(self, slackObj):
        # Gets the search parameters from within the quotations of a Slack message.
        print "Searching..."
        _regex = ur'((\"|\u201c)(.*?)\")'
        _regexc = re.compile(_regex, re.UNICODE)
        self.string = re.search(_regexc, slackObj.text)
        if self.string and (self.jira or self.zd or self.sfdc):
            self.string = self.string.group(3)
            return True
        else:
            return False

    def getLimit(self, slackObj):
        # Checks for a reply limit being specified in the Slack message.
        self.result_limit = re.search(r'limit=(\d+|none)', slackObj.text, re.I)
        if self.result_limit:
            print "Found a result limit of %s" % self.result_limit.group(1)
            self.result_limit = self.result_limit.group(1)
            try:
                # Check if the limit was an integer.
                self.result_limit = int(self.result_limit)
            except ValueError:
                # Therefore must be "none" so set it to a stupidly large number
                self.result_limit = 999999
            print "Using a result limit of %s of type %s" % (self.result_limit, type(self.result_limit))
        else:
            print "Using default result limit of %s" % result_limit
            self.result_limit = result_limit

class sfdc(object):

    def __init__(self, options):
        self.options = options

        self.sf = Salesforce(username=options["username"], password=options["password"],
                             security_token=options["token"])

    def getRecords(self, query):
        self.query = query
        print "Searching SFDC for %s" % self.query

        self.contacts = []
        self.accounts = []
        self.users = []

        results = self.sf.quick_search(self.query)
        if not results:
            return False

        for record in results:
            if record['attributes']['type'] == 'Contact':
                self.contacts.append(self.sf.Contact.get(record["Id"]))
            elif record['attributes']['type'] == 'User':
                self.users.append(self.sf.User.get(record["Id"]))
            elif record['attributes']['type'] == 'Account':
                self.accounts.append(self.sf.Account.get(record["Id"]))

        return True

def main():

    # Get all ZD Users and Orgs
    if not (os.path.isfile("/tmp/zd_users_list.pickle") and os.path.isfile("/tmp/zd_orgs_list.pickle")) \
            and arguments["--refresh-cache"]:
        zd_users_list = getZDOutput(zd_credentials, zd_params, "users")
        zd_orgs_list = getZDOutput(zd_credentials, zd_params, "organizations")
        pickle.dump(zd_users_list, open("/tmp/zd_users_list.pickle", 'wb'))
        pickle.dump(zd_orgs_list, open("/tmp/zd_orgs_list.pickle", 'wb'))
    else:
        zd_users_list = pickle.load(open("/tmp/zd_users_list.pickle", 'rb'))
        zd_orgs_list = pickle.load(open("/tmp/zd_orgs_list.pickle", 'rb'))

    global zd_users
    zd_users = {}
    for user in zd_users_list:
        zd_users[user["id"]] = {"name": user["name"], "email" : user["email"]}

    global zd_orgs
    zd_orgs = {}
    for org in zd_orgs_list:
        zd_orgs[org["id"]] = {"name": org["name"]}

    del(zd_users_list)
    del(zd_orgs_list)

    # Instantiate Slack API object
    global rocketsearch
    rocketsearch = SlackClient(slackToken)

    # Connect to Slack Real-Time Messaging
    if rocketsearch.rtm_connect():
        print("RocketSearch: connected and running!")
        while True:
            try:
                # Create message objects for any incoming messages. Other RTM events trigger an exception.
                message = slack(message=rocketsearch.rtm_read()[0])
                print message.message
                # If it is a message and the bot didn't sent it, continue.
                if message and message.text and not message.isBot:
                    # Check whether the bot was invoked.
                    if message.checkInvoked():
                        # If so, check whether there were quotes in the message. If not, read back later.
                        if not message.search.getSearchParams(message) and message.search.invoked:
                            if message.search.help:
                                if not message.isDM:
                                    message.response("Happy to help. Check your direct messages.")
                                    # Update destination channel to the user's ID, thus sending a direct message.
                                    message.channel = message.user
                                message.response(help_string)
                            else:
                                message.response("No search parameters found.")
                            sleep(1)
                            continue
                        # Check for a message specified result limit
                        message.search.getLimit(message)
                        # Check to ensure there's no characters we can't turn into a URL.
                        try:
                            str(message.search.string)
                        except UnicodeEncodeError as e:
                            message.search.string = message.search.string.encode("ascii", "ignore")
                            print message.search.string, type (message.search.string)
                        # Run the search parameters against the Zendesk Query API
                        if message.search.zd:
                            zd_params["query"] = message.search.string
                            zd_data = getZDOutput(zd_credentials, zd_domain, "search", params=zd_params)
                            zd_tickets = parseZDOutput(zd_data)
                            if zd_tickets:
                                message.response(respondZDData(zd_tickets, message.search.result_limit))
                            else:
                                message.response("No results in Zendesk for your search.")
                        # Run the search parameters against the JIRA Search API
                        if message.search.jira:
                            global jira
                            jira = connectToJira(jr_options)
                            # Get JIRA ticket IDs which match the search
                            try:
                                jr_tickets = getJiraTickets(jira, message.search.string, message.search.textonly)
                            except JIRAError as e:
                                # Problem with the query string are returned as JIRAError objects
                                message.response("*Error with JIRA Search*: _%s_" % e.text)
                                sleep(1)
                                continue
                            jr_response = ""
                            result = 0
                            for ticket in jr_tickets:
                                # Create ticket objects with populated fields based on JIRA ticket ID.
                                ticket = jira_bug(ticket)
                                if result < message.search.result_limit:
                                    result += 1
                                    print "Result number %d of limit %d" % (result, message.search.result_limit)
                                    jr_response = jr_response + ticket.respondBugDetails()
                            if jr_response:
                                message.response(jr_response)
                            else:
                                message.response("No results in JIRA for your search.")
                        if message.search.sfdc:
                            sfdata = sfdc(sf_options)
                            if not sfdata.getRecords(message.search.string):
                                message.response("No results in Salesforce for your search")
                                sleep(1)
                                continue
                            else:
                                sf_response = ""
                                if sfdata.accounts:
                                    sf_response += "`Accounts`\n"
                                    for record in sfdata.accounts:
                                        sf_response += "<https://%s/%s|%s>\n>*Licenses*: %s\n>*Account Manager*: %s\n" \
                                                        % (sfdata.sf.sf_instance, record["Id"], record["Name"],
                                                        record["Active_Support_Licenses__c"].replace("\n", " "),
                                                        record["Account_Manager__c"])
                                if sfdata.contacts:
                                    sf_response += "`Contacts`\n"
                                    for record in sfdata.contacts:
                                        sf_response += "<https://%s/%s|%s>\n>*Email*: %s\n" \
                                                       % (sfdata.sf.sf_instance, record["Id"], record["Name"],
                                                          record["Email"])
                                if sfdata.users:
                                    sf_response += "`Users`\n"
                                    for record in sfdata.users:
                                        sf_response += "<https://%s/%s|%s>\n>*Email*: %s\n" \
                                                       % (sfdata.sf.sf_instance, record["Id"], record["Name"],
                                                          record["Email"])
                                message.response(sf_response)
                        sleep(1)
                else:
                    print message
            except (IndexError, KeyError) as e:
                print str(e)
                pass
            sleep(1)

if __name__ == "__main__":

    arguments = docopt(__doc__)

    ### Configuration ###
    if "~" in arguments['--config']:
        pattern = re.compile('~')
        arguments['--config'] = pattern.sub(os.path.expanduser("~"), arguments['--config'])
    if not os.path.exists(arguments['--config']):
        logger.error("Specified configuration file does not exist!")
        exit(1)
    with open(arguments['--config'], 'r') as ymlfile:
        cfg = yaml.load(ymlfile)

    ### Zendesk ###
    zencfg = cfg["zendesk"]
    global zd_domain
    zd_domain = zencfg["subdomain"]
    global zd_credentials
    zd_credentials = zencfg["email"], zencfg["password"]
    global zd_params
    zd_params = {
        'sort_by': 'created_at',
        'sort_order': 'desc'
    }

    ### JIRA ###
    jrcfg = cfg["jira"]
    jr_options = {
        "server": jrcfg["server"],
        "username": jrcfg["username"],
        "password": jrcfg["password"],
    }

    ### Slack ###
    slkcfg = cfg["slack"]
    global slackToken
    slackToken = slkcfg['token']
    global slackBot
    slackBot = slkcfg["bot_id"]

    ### SalesForce ###
    sfcfg = cfg["salesforce"]
    sf_options = {
        "username" : sfcfg["username"],
        "password" : sfcfg["password"],
        "token" : sfcfg["security_token"]
    }

    ### General ###
    gencfg = cfg["general"]
    global result_limit
    result_limit = gencfg["result_limit"]

    help_string = """*_A handy Slack bot made by slaffer to search Zendesk and JIRA._*

*Full Examples (i.e. the TL;DR notes):*
>In a channel:
>`@rocketsearch zendesk "assignee:slaffer@cumulusnetworks.com vxlan qinq" limit=2`
>`@rocketsearch jira "reporter = slaffer AND project = CM AND text ~ 'vxlan'"`
>`@rocketsearch text "snmp bgp mibs" limit=3`
>Directly:
>`zendesk "requester:ben.jackson@slicedtech.com.au type:ticket console locks up"`
>`jira "labels in (customer-found, gss, scrub) AND project = 'CM'" limit=none`
>`text "mellanox vxlan udp source port"`

``` ```
*Getting Stated:*
> Open a direct message to me or invite me to a channel.

*How to Search:*
> Choose your provider:
>  1) Zendesk (GSS Cases)
>  2) JIRA (Tickets)
>  3) Text (Search both Zendesk and JIRA for text)
>
> If you're messaging me directly, simply type your provider followed by your search query in quotes.
> If you're in a channel, tag me, type your operator and then your query.

*Searching Zendesk:*
Zendesk searches can be text only or can include certain operators outlined in the <https://support.ze\
ndesk.com/hc/en-us/articles/203663226|Search Reference>. Only tickets are returned in newest to oldest.
> In a channel:
>  - `@rocketsearch search zendesk for "<query>"`
>     or simply
>  - `@rocketsearch zendesk "<query>"`
> Directly:
>  - `zendesk "query"`

*Searching JIRA:*
JIRA searches are done in the JIRA Query Langauage (<https://confluence.atlassian.com/jirasoftwarecloud\
/advanced-searching-764478330.html#Advancedsearching-Understandingadvancedsearching|JQL>)
> In a channel:
>  - `@rocketsearch jira "<JQL query>"`
> Directly:
>  - `jira "<JQL query>"`

*Searching for Text:*
This searches both Zendesk and JIRA for the following text. Multiple words or strings are considered as ANDs.
> In a channel:
>  - `@rocketsearch text "<words>"`
> Directly:
>  - `text "<words>"`

*Limiting results:*
By default, a maximum of 5 results are returned per provider. You can change this limit by appending\
 `limit=<number>` or `limit=None` to your query.
> In a channel:
>  - `@rocketsearch text "<words>" limit=10`
> Directly:
>  - `text "<words>" limit=none`


*Bugs and Feature Requests:*
Please open a JIRA ticket in the GSS project and assign it to @slaffer.
"""

    main()
    exit(0)
