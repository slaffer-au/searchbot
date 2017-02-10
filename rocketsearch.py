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
from jira import JIRA
from slackclient import SlackClient
from time import sleep


def getZDOutput(credentials, subdomain):

    session = requests.Session()
    session.auth = credentials

    url = 'https://'+ zd_domain +'.zendesk.com/api/v2/search.json?' + urlencode(zd_params)
    print url

    response = session.get(url)
    if response.status_code != 200:
        print('Status:', response.status_code, 'Problem with the request. Exiting.')
        return None

    # Get all responses as JSON
    data = response.json()
    return data

def parseZDOutput(data):

    tickets = []
    others = []
    for result in data['results']:
        if result['result_type'] == "ticket":
            tickets.append(result)
        else:
            others.append(result)
    return tickets

def printZDData(tickets):

    #Fields I care about
    t_fields = ['id', 'subject', 'submitter_id','assignee_id', 'status', 'description']

    for ticket in tickets:
        for field in t_fields:
            if field == "description":
                print field.capitalize(), ": ", ticket[field][:100].replace('\n', ' ')
            else:
                print field.capitalize(), ": ", ticket[field]
        print ""

def respondZDData(tickets):

    #Fields I care about
    t_fields = ['id', 'subject', 'submitter_id','assignee_id', 'status', 'description']
    response = ""

    for ticket in tickets:
        for field in t_fields:
            if field == "description":
                response = response + str(field.capitalize()) + ": " + ticket[field][:100].replace('\n', ' ').replace("\r", "") + "\n"
            else:
                response = response + str(field.capitalize()) + ": " + str(ticket[field]) + "\n"
        response = response + "\n"
    print response
    return response




def connectToJira(options):
    jira = JIRA(server=options['server'], basic_auth=(options['username'], options['password']))
    return jira

def getJiraTickets(jira, search_str):
    tickets = jira.search_issues(search_str)
    return tickets

class jira_bug:

    def __init__(self, id):
        self.id = id
        self.jiraObj = jira.issue(self.id)
        self.fields = vars(self.jiraObj.fields)

    def bugDetails(self, jr_fields):
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
            except (AttributeError, TypeError) as e:
                if "NoneType" in str(e):
                    pass
                else:
                    raise
        print("\n")

def getSlackChannelList():
    response = {}
    url = "https://slack.com/api/channels.list?token={}".format(slackToken)
    public = requests.get(url).json()
    for channel in public['channels']:
        response[channel['name']] = channel['id']
    url = "https://slack.com/api/groups.list?token={}".format(slackToken)
    private = requests.get(url).json()
    for group in private['groups']:
        response[group['name']] = group['id']
    return response

class slack:

    def __init__(self, message):
        self.message = message
        self.text = self.message["text"]
        self.channel = self.message["channel"]
        self.user = self.message["user"]
        self.isBot = False
        if slackBot == str(self.user):
            self.isBot = True


    def getChannelType(self):
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
        self.getChannelType()
        print "made it past channel selection"
        self.search = search(self)
        if self.search.invoked:
            return True

    def response(self, string):
        self.response = rocketsearch.rtm_send_message(self.channel, string)

class search:
    def __init__(self, slackObj):
        self.invoked = True
        self.zd = False
        self.jira = False
        self.help = False

        if slackObj.isDM and re.search(r'zendesk', slackObj.text, re.I):
            print "DM zendesk: " + slackObj.text
            self.zd = True
        elif slackObj.isDM and re.search(r'jira', slackObj.text, re.I):
            print "DM zendesk: " + slackObj.text
            self.jira = True
        elif re.search(r'^(<@U43QEBKQE>.*?zendesk)', slackObj.text, re.I):
            print "Channel zendesk: " + slackObj.text
            self.zd = True
        elif re.search(r'^(<@U43QEBKQE>.*?jira)', slackObj.text, re.I):
            print "Channel zendesk: " + slackObj.text
            self.jira = True
        else:
            print "I was not invoked or source was selected"

    def getSearchParams(self, slackObj):
        print "Searching..."
        self.string = re.search(r'\".*?\"', slackObj.text)
        if self.string:
            self.string = self.string.group()
            return True
        else:
            return False

def main():
    global rocketsearch
    rocketsearch = SlackClient(slackToken)

    if rocketsearch.rtm_connect():
        print("RocketSearch: connected and running!")
        while True:
            try:
                message = slack(message=rocketsearch.rtm_read()[0])
                if message and message.text and not message.isBot:
                    if message.checkInvoked():
                        if not message.search.getSearchParams(message):
                            message.response("No search parameters found")
                            sleep(1)
                            continue
                        if message.search.zd:
                            zd_params["query"] = message.search.string
                            zd_data = getZDOutput(zd_credentials, zd_domain)
                            zd_tickets = parseZDOutput(zd_data)
                            if zd_tickets:
                                message.response(respondZDData(zd_tickets))

                else:
                    print message
            except KeyboardInterrupt:
                break
            except Exception as e:
                print str(e)
                pass
            sleep(1)





#    global jira
#    jira = connectToJira(jr_options)
#    jr_tickets = getJiraTickets(jira, search_params)

#   jr_fields = ['summary', 'status', 'reporter', 'assignee', 'customfield_10602', 'description',]
#    for ticket in jr_tickets:
#        ticket = jira_bug(ticket)
#        ticket.bugDetails(jr_fields)


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
        'sort_order': 'asc'
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

    main()
    exit(0)