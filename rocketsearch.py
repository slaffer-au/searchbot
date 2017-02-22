#!/usr/bin/env python

"""
Usage:
    rocketsearch.py [options]

Options:
    -h, --help                                     Show this help message and exit.
    -l LEVEL, --level LEVEL                        Logging level during execution. Available options: DEBUG, INFO, WARNING, ERROR (default), CRITICAL [default: WARNING]
    -c CONFIGFILE, --config CONFIGFILE             Provide a file containing credentials and settings [default: ./rocketsearch.yml]
    --list-channels                                Do not start the Slack bot, instead return a list of the current channels. Used to determine channel id for configuration.
"""

import requests, re, yaml, os, pprint
from docopt import docopt
from urllib import urlencode
from jira import JIRA, JIRAError
from slackclient import SlackClient
from time import sleep

def getZDOutput(credentials, subdomain):
    # Use Zendesk Query API to search
    session = requests.Session()
    session.auth = credentials

    url = 'https://'+ zd_domain +'.zendesk.com/api/v2/search.json?' + urlencode(zd_params)
    print url

    response = session.get(url)
    if response.status_code != 200:
        print('Status:', response.status_code, 'Problem with the request. Exiting.')
        return None

    # Get all responses as JSON and convert to dict
    data = response.json()
    return data

def parseZDOutput(data):
    # Search for tickets in Zendesk query results
    tickets = []
    others = []
    for result in data['results']:
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
    t_fields = ['id', 'subject', 'submitter_id','assignee_id', 'status', 'description']
    response = ""
    result = 0

    for ticket in tickets:
        if result < result_limit:
            result += 1
            for field in t_fields:
                if field == "id":
                    human_url = "https://cumulusnetworks.zendesk.com/agent/tickets/"+str(ticket[field])
                    response = response+"*"+str(field.capitalize())+"*: <"+human_url+"|"+str(ticket[field])+">\n"
                elif field == "description":
                    response = response + "*"+str(field.capitalize())+"*" + ": " + ticket[field]\
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
    # Uses JIRA API to search for tickets matching the JRQ language query string. Returns a list of JIRA Issue objects.
    if text_only:
        # Searches based on text only. Shortcuts full JRQ.
        tickets = jira.search_issues("text ~ '%s'" % search_str)
    else:
        # Must be a full JRQ query.
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
        print "Response to channel %s is: %s" % (self.channel, string)
        rocketsearch.api_call("chat.postMessage", channel=self.channel, text=string, as_user=True, unfurl_links=False)

class search:
    # Determines and assigns search parameters to the Slack messages. Determine whether bot was invoked.
    def __init__(self, slackObj):
        self.invoked = True
        self.zd = False
        self.jira = False
        self.textonly = False
        self.help = False

        if slackObj.isDM and re.search(r'zendesk', slackObj.text, re.I):
            print "DM zendesk: " + slackObj.text
            self.zd = True
        elif slackObj.isDM and re.search(r'jira', slackObj.text, re.I):
            print "DM jira: " + slackObj.text
            self.jira = True
        elif slackObj.isDM and re.search(r'text', slackObj.text, re.I):
            print "DM text: " + slackObj.text
            self.textonly = True
            self.zd = True
            self.jira = True
        elif re.search(r'^(<@U43QEBKQE>.*?zendesk)', slackObj.text, re.I):
            print "Channel zendesk: " + slackObj.text
            self.zd = True
        elif re.search(r'^(<@U43QEBKQE>.*?jira)', slackObj.text, re.I):
            print "Channel jira: " + slackObj.text
            self.jira = True
        elif re.search(r'^(<@U43QEBKQE>.*?text)', slackObj.text, re.I):
            print "Channel text: " + slackObj.text
            self.textonly = True
            self.zd = True
            self.jira = True
        else:
            self.invoked = False
            print "I was not invoked or source was selected"

    def getSearchParams(self, slackObj):
        # Gets the search parameters from within the quotations of a Slack message.
        print "Searching..."
        _regex = ur'((\"|\u201c)(.*?)\")'
        _regexc = re.compile(_regex, re.UNICODE)
        self.string = re.search(_regexc, slackObj.text)
        if self.string and (self.jira or self.zd):
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

def main():

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
                            message.response("No search parameters found.")
                            sleep(1)
                            continue
                        # Check for a message specified result limit
                        message.search.getLimit(message)
                        # Run the search parameters against the Zendesk Query API
                        if message.search.zd:
                            zd_params["query"] = message.search.string
                            zd_data = getZDOutput(zd_credentials, zd_domain)
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
                        sleep(1)
                else:
                    print message
            except (IndexError, KeyError) as e:
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

    ### General ###
    gencfg = cfg["general"]
    global result_limit
    result_limit = gencfg["result_limit"]

    main()
    exit(0)