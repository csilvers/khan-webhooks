import cgi
import logging
import re
import sys

try:
    import secrets
except ImportError:
    print ("secrets.py is missing -- copy and tweak the template from "
            "secrets.py.example.")
    raise

sys.path.insert(1, 'third_party')
from third_party.flask import Flask
from third_party.flask import request
from third_party import phabricator
from third_party import requests

app = Flask(__name__)


def _username_from_phid(phid):
    phab = phabricator.Phabricator(
            host=secrets.phabricator_host + '/api/',
            username=secrets.phabricator_username,
            certificate=secrets.phabricator_certificate,
        )
    resp = phab.phid.lookup(names=[phid]).response
    if phid in resp:
        return resp[phid]['name']


def _link_html(url, text):
    return '<a href="%s">%s</a>' % (cgi.escape(url, True), cgi.escape(text))


def _send_to_hipchat(message, room, from_name):
    resp = requests.post(
        "https://api.hipchat.com/v1/rooms/message?auth_token=%s" %
            secrets.hipchat_token,
        data={
            'from': from_name,
            'room_id': room,
            'color': 'yellow',
            'message_format': 'html',
            'message': message
        })
    logging.info("Sent to HipChat: %s", resp.text)


# Add me to feed.http-hooks in Phabricator config
@app.route('/phabricator-feed', methods=['POST'])
def phabricator_feed():
    logging.info("Processing %s" % request.form)
    # TODO(alpert): Consider using native Phabricator Jabber support
    # https://secure.phabricator.com/T1271 when it happens.
    if (request.form['storyType'] ==
            'PhabricatorApplicationTransactionFeedStory'):
        def linkify(match):
            url = "%s/%s" % (secrets.phabricator_host, match.group(3))
            return """%(pre)s%(link)s.""" % {
                    'pre': match.group(1),
                    'link': _link_html(url, match.group(2)),
                }

        message, replaced = re.subn(
            r"^([a-zA-Z0-9.]+ (?:created|abandoned) )"
            r"((D[0-9]+): .*)\.$",
            linkify,
            request.form['storyText'])

        if replaced:
            # TODO(alpert): Different rooms for different repos?
            _send_to_hipchat(message, '1s and 0s', 'Phabricator Fox')

    return ''


# Add me as a GitHub web hook
@app.route('/github-feed', methods=['POST'])
def github_feed():
    event_type = request.headers.get('X-GitHub-Event')
    # payload looks like https://gist.github.com/spicyj/6c9c13af85771f4fcd39
    payload = request.json
    logging.info("Processing %s: %s", event_type, payload)
    if event_type != 'push':
        logging.info("Skipping event type %s", event_type)
        return ''

    if not payload['ref'].startswith('refs/heads/'):
        logging.info("Skipping ref %s", payload['ref'])
        return ''

    branch = payload['ref'][len('refs/heads/'):]
    # Like "Khan/webapp"
    short_repo_name = "%s/%s" % (payload['repository']['owner']['name'],
                                 payload['repository']['name'])

    old_commits = [c for c in payload['commits'] if not c['distinct']]
    new_commits = [c for c in payload['commits'] if c['distinct']]

    branch_link_html = _link_html(
        "%s/tree/%s" % (payload['repository']['url'], branch), branch)
    repo_html = _link_html(payload['repository']['url'], short_repo_name)
    before_html = _link_html(
        "%s/commit/%s" % (payload['repository']['url'], payload['before']),
        payload['before'][:6])
    after_html = _link_html(
        "%s/commit/%s" % (payload['repository']['url'], payload['after']),
        payload['after'][:6])

    if payload['created']:
        verb_html = "created branch %s of %s" % (branch_link_html, repo_html)
    elif payload['deleted']:
        verb_html = "deleted branch %s of %s" % (
            cgi.escape(branch, True), repo_html)
    elif payload['forced']:
        verb_html = "force-pushed branch %s of %s from %s to %s" % (
            branch_link_html, repo_html, before_html, after_html)
    elif new_commits:
        verb_html = "pushed to branch %s of %s" % (branch_link_html, repo_html)
    else:
        verb_html = "fast-forward pushed branch %s of %s to %s" % (
            branch_link_html, repo_html, after_html)

    username = payload['pusher']['name']
    username_link_html = _link_html(
        "https://github.com/%s" % username, username)

    html_lines = []
    html_lines.append("%s %s" % (username_link_html, verb_html))

    COMMITS_TO_SHOW = 5

    for commit in new_commits[:COMMITS_TO_SHOW]:
        MAX_LINE_LENGTH = 60
        commit_message = commit['message']
        if '\n' in commit_message:
            commit_message = commit_message[:commit_message.index('\n')]
        if len(commit_message) > MAX_LINE_LENGTH:
            commit_message = commit_message[:MAX_LINE_LENGTH - 3] + '...'

        html_lines.append("- %s (%s)" % (
            cgi.escape(commit_message, True),
            _link_html(commit['url'], commit['id'][:7])))

    if old_commits:
        # If this is a fast-forward push, omit the "and"
        and_text = "and " if new_commits else ""
        if len(old_commits) == 1:
            html_lines.append("- %s1 existing commit" % and_text)
        else:
            html_lines.append("- %s%s existing commits" %
                              (and_text, len(old_commits)))

    if len(new_commits) > COMMITS_TO_SHOW:
        html_lines.append("- and %s more..." %
                          (len(new_commits) - COMMITS_TO_SHOW))

    message_html = '<br>'.join(html_lines)

    # TODO(alpert): More elaborate configuration? We'll see if this gets
    # unmanageable.
    _send_to_hipchat(message_html, '1s and 0s', 'GitHub')
    if short_repo_name == 'Khan/webapp' and (
            (branch + '-').startswith('athena-')):
        _send_to_hipchat(message_html, 'Athena', 'GitHub')

    return ''

if __name__ == '__main__':
    app.run(debug=True)
