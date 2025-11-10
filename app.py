# --- IMPORTS & ASYNC CONFIGURATION ---
import eventlet  # Required for SocketIO with eventlet async mode
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, get_flashed_messages
)
from flask_socketio import SocketIO, emit
from datetime import datetime
import json
import logging
import uuid
import time  # Not strictly used, but kept from original

import json

# Set up basic logging for visibility
logging.basicConfig(level=logging.INFO)

# --- APPLICATION & SOCKETIO SETUP ---
app = Flask(__name__)
# WARNING: Replace this with a secure, long, random key in production
app.config['SECRET_KEY'] = 'A_VERY_SECRET_KEY_FOR_MUN_APP'

# Configure SocketIO explicitly for eventlet async mode and allow all origins for testing
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# --- GLOBAL CONFIGURATION ---
ADMIN_USER = 'ADMIN'
# Updated list of valid delegate names

# NOTE: Assumes delegates.json exists and is structured correctly
try:
    with open("delegates.json") as file:
        dele = json.load(file)
        VALID_DELEGATES = [x.upper() for x in list(dele["Delegates"].keys())]
except FileNotFoundError:
    logging.warning("delegates.json not found. Using default delegate list for testing.")
    VALID_DELEGATES = ['FRANCE', 'ISRAEL', 'AUSTRALIA', 'INDIA', 'CHINA']

# --- GLOBAL STATE (In-memory Database Stand-in) ---
# Stores all submitted resolutions, amendments, announcements, and results
mun_documents = []

# Tracks the current vote status (global state)
# CHANGED: 'voters' is now a dictionary to store {delegate_id: vote_choice}
current_vote_tally = {'yay': 0, 'nay': 0, 'abstain': 0, 'voters': {}}
current_vote_target_id = None  # ID of the document currently being voted on


# --- UTILITY FUNCTIONS ---

def get_current_user_id():
    """Retrieves the current user ID (Delegate/Admin) from the session."""
    return session.get('user', 'Guest')


def get_document_by_id(doc_id):
    """Retrieves a document from the stream by its ID."""
    return next((doc for doc in mun_documents if doc.get('id') == doc_id), None)


def render_stream():
    """
    Renders the current state of the document stream into HTML.
    Applies Tailwind-like classes for visual styling based on document type.
    """
    # Using Tailwind-like classes for aesthetics
    html_content = '<div class="space-y-4 font-sans max-w-2xl mx-auto">'
    # Iterate in reverse to show newest items first
    for doc in reversed(mun_documents):
        # Determine color/style based on type
        bg_class = 'bg-gray-50 border-gray-300'
        title_tag = 'h3'
        border_color = 'border-l-4'
        vote_status = ''

        if doc['type'] == 'resolution':
            bg_class = 'bg-blue-50 border-blue-500'
        elif doc['type'] == 'amendment':
            bg_class = 'bg-yellow-50 border-yellow-500'
        elif doc['type'] == 'vote_result':
            # Check the content for the result status for specific styling
            if 'Result: PASSED' in doc.get('content', ''):
                bg_class = 'bg-green-100 border-green-700'
            elif 'Result: FAILED' in doc.get('content', ''):
                bg_class = 'bg-red-100 border-red-700'
            else:
                bg_class = 'bg-gray-100 border-gray-500'
        elif doc['type'] == 'moderator_announcement':
            bg_class = 'bg-red-50 border-red-500'

        # Check if this document is the current vote target
        if globals().get('current_vote_target_id') == doc.get('id'):
            vote_status = (
                '<span class="text-xs font-bold text-red-600 '
                'bg-red-100 px-2 py-0.5 rounded-full ml-2">VOTING ACTIVE</span>'
            )

        doc_id_display = f'<span class="text-xs text-gray-400 ml-2">ID: {doc.get("id", "N/A")}</span>'

        # Construct the HTML block for the document
        html_content += f"""
        <div class="p-4 rounded-lg shadow-md {bg_class} {border_color}">
        <{title_tag} class="text-lg font-bold text-gray-900">{doc['title']}{vote_status}</{title_tag}>
        <p class="text-sm text-gray-600 mt-1">
        <span class="font-medium text-gray-900">{doc['delegate']}</span>
        ({doc['type'].replace('_', ' ').title()}) -
        <span class="text-xs text-gray-500">{doc['timestamp']}</span>
        {doc_id_display}
        </p>
        <p class="mt-2 text-gray-700 whitespace-pre-wrap text-base leading-relaxed">{doc.get('content', '')}</p>
        </div>
        """
    html_content += '</div>'
    return html_content


def get_votable_documents():
    """Returns a list of documents that can be voted on (Resolutions and Amendments)."""
    return [
        doc for doc in reversed(mun_documents)
        if doc['type'] in ['resolution', 'amendment']
    ]


def broadcast_stream():
    """
    Emits the updated document stream to all connected clients.
    Used to update the Dashboard and Admin pages.
    """
    stream_html = render_stream()
    socketio.emit('stream_update', {'data': stream_html}, broadcast=True)


# --- FLASK ROUTES: AUTHENTICATION & NAVIGATION ---

@app.route('/')
def index():
    """Root route: Redirects based on session status."""
    if 'user' in session;l:
        return redirect(url_for('admin_page') if session.get('role') == 'admin' else url_for('delegate_page'))
    return redirect(url_for('login'))


@app.route('/roster')
def delegate_roster():
    """Loads delegate data from JSON and renders the HTML template."""
    return render_template(
        'delegate_roster.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handles user login for Admin or Delegates."""
    if request.method == 'POST':
        username = request.form.get('username', '').upper().strip()

        if username == ADMIN_USER:
            session.update({'user': username, 'role': 'admin'})
            flash(f'Logged in as Chairman ({username}).', 'success')
            return redirect(url_for('admin_page'))

        elif username in VALID_DELEGATES:
            session.update({'user': username, 'role': 'delegate'})
            flash(f'Logged in as Delegate for {username}.', 'success')
            return redirect(url_for('delegate_page'))

        else:
            flash('Invalid Delegate ID or Admin code.', 'error')

    messages = [(msg, category) for msg, category in get_flashed_messages(with_categories=True)]
    return render_template('login.html', messages=messages)


@app.route('/logout')
def logout():
    """Clears the session and logs the user out."""
    session.pop('user', None)
    session.pop('role', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


@app.route('/delegate')
def delegate_page():
    """Delegate's main interface."""
    if session.get('role') != 'delegate':
        flash('Access denied. Please log in as a delegate.', 'error')
        return redirect(url_for('login'))

    messages = [(msg, category) for msg, category in get_flashed_messages(with_categories=True)]
    return render_template('delegate.html', delegate_id=session['user'], messages=messages)


@app.route('/admin')
def admin_page():
    """Admin/Chairman's main interface."""
    if session.get('role') != 'admin':
        flash('Access denied. Please log in as the Administrator.', 'error')
        return redirect(url_for('login'))

    messages = [(msg, category) for msg, category in get_flashed_messages(with_categories=True)]
    votable_docs = get_votable_documents()
    current_target = get_document_by_id(current_vote_target_id)

    return render_template('admin.html',
                           delegate_id=session['user'],
                           messages=messages,
                           votable_docs=votable_docs,
                           current_vote_target=current_target)


@app.route('/dashboard')
def dashboard():
    """Publicly visible document stream and vote monitor."""
    return render_template('dashboard.html', stream_content=render_stream())


# --- FLASK ROUTES: API ENDPOINTS FOR POLLING ---

@app.route('/stream_content_api')
def stream_content_api():
    """Returns the raw HTML content of the document stream for AJAX polling."""
    return render_stream()


@app.route('/vote_status_api')
def vote_status_api():
    """Returns the current voting status and tally as JSON."""
    global current_vote_target_id, current_vote_tally

    if not current_vote_target_id:
        return json.dumps({
            'active': False,
            'target_title': None,
            'tally': None,
            'voted': False
        })

    target_doc = get_document_by_id(current_vote_target_id)
    target_title = target_doc['title'] if target_doc else "Unknown Document"
    delegate_id = get_current_user_id()

    # Determine if the current delegate has voted
    # CHANGED: Check in the 'voters' dictionary
    has_voted = delegate_id in current_vote_tally['voters']

    return json.dumps({
        'active': True,
        'target_title': target_title,
        'tally': {
            'yay': current_vote_tally['yay'],
            'nay': current_vote_tally['nay'],
            'abstain': current_vote_tally['abstain'],
            'voter_count': len(current_vote_tally['voters']),
            'total_delegates': len(VALID_DELEGATES)
        },
        'voted': has_voted
    })


# --- SOCKETIO EVENT HANDLERS ---

@socketio.on('connect')
def handle_connect():
    """Handles new client connection and informs them of the current vote status."""
    user = get_current_user_id()
    app.logger.info(f'{user} connected.')

    # Inform the newly connected client if a vote is active
    global current_vote_target_id
    if current_vote_target_id:
        target_doc = get_document_by_id(current_vote_target_id)
        if target_doc:
            emit('vote_started', {'target': target_doc['title']})


@socketio.on('mun_submission')
def handle_mun_submission(data):
    """
    Handles submissions from delegates: resolutions, amendments, and votes.
    """
    global current_vote_target_id, current_vote_tally

    submission_type = data.get('type')
    delegate_id = session.get('user')

    # Basic authorization check
    if not delegate_id or delegate_id not in VALID_DELEGATES and delegate_id != ADMIN_USER:
        emit('feedback', {'message': 'Authentication error.'})
        return

    # --- Vote Submission ---
    if submission_type == 'vote':
        vote = data.get('vote')
        target_doc = get_document_by_id(current_vote_target_id)

        if not current_vote_target_id or not target_doc:
            emit('feedback', {'message': 'No formal vote is currently active.'})
            return

        # CHANGED: Check if the delegate has already voted (in dict keys)
        if delegate_id in current_vote_tally['voters']:
            emit('feedback', {'message': 'You have already cast your vote.'})
            return

        # Process the vote (yay, nay, or abstain)
        if vote in ['yay', 'nay', 'abstain']:
            current_vote_tally[vote] += 1
            # CHANGED: Store the delegate's vote choice in the dictionary
            current_vote_tally['voters'][delegate_id] = vote
            emit('feedback', {'message': f'Vote recorded: {vote.upper()} on {target_doc["title"]}'})

            # Broadcast the live tally update to the admin page
            socketio.emit('vote_tally_update', {
                'target_id': current_vote_target_id,
                'target_title': target_doc['title'],
                # Removed individual vote counts to encourage admin to use the API or refresh
                'voter_count': len(current_vote_tally['voters']),
                'total_delegates': len(VALID_DELEGATES)
            }, broadcast=True)
            return

    # --- Resolution/Amendment Submission ---
    elif submission_type in ['resolution', 'amendment']:
        new_doc = {
            'id': str(uuid.uuid4()),
            'type': submission_type,
            'title': data.get('title'),
            'content': data.get('content'),
            'delegate': delegate_id,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        mun_documents.append(new_doc)

        emit('feedback', {'message': f'{submission_type.title()} "{new_doc["title"]}" submitted.'}, broadcast=False)
        broadcast_stream()  # Update Admin/Dashboard stream
        return

    emit('feedback', {'message': 'Invalid submission type.'})


@socketio.on('moderator_action')
def handle_moderator_action(data):
    """Handles actions specific to the Admin (Chairman) role."""
    global mun_documents, current_vote_target_id, current_vote_tally

    if session.get('role') != 'admin':
        emit('feedback', {'message': 'Unauthorized action.'})
        return

    action = data.get('action')

    # --- Start Vote Action ---
    if action == 'start_vote':
        target_doc_id = data.get('target_id')
        target_doc = get_document_by_id(target_doc_id)

        if not target_doc or target_doc['type'] not in ['resolution', 'amendment']:
            emit('feedback', {'message': 'Invalid document ID or document type for voting.'})
            return

        # Reset and activate the vote state
        current_vote_target_id = target_doc_id
        # CHANGED: Reset 'voters' to an empty dictionary
        current_vote_tally = {'yay': 0, 'nay': 0, 'abstain': 0, 'voters': {}}
        target_title = target_doc['title']

        # Announce the vote start to all clients
        socketio.emit('vote_started', {'target': target_title}, broadcast=True)
        emit('feedback', {'message': f'Formal vote on "{target_title}" started.'})

        # Add an announcement to the stream
        new_doc = {
            'id': str(uuid.uuid4()),
            'type': 'moderator_announcement',
            'title': 'VOTE STARTED',
            'content': (
                f'A formal vote is now open on the **{target_doc["type"].upper()}**: '
                f'{target_title}. All delegates must cast their vote (YAY/NAY/ABSTAIN).'
            ),
            'delegate': 'CHAIRMAN',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        mun_documents.append(new_doc)
        broadcast_stream()

    # --- Finalize Vote Action ---
    elif action == 'finalize_vote':
        target_doc = get_document_by_id(current_vote_target_id)
        if not current_vote_target_id or not target_doc:
            emit('feedback', {'message': 'No vote is currently active to finalize.'})
            return

        target_title = target_doc['title']

        # Calculate the result
        yay = current_vote_tally['yay']
        nay = current_vote_tally['nay']
        abstain = current_vote_tally['abstain']
        total_votes = yay + nay + abstain
        # Result logic: Simple majority of Yay vs Nay votes
        result = 'PASSED' if yay > nay else 'FAILED'

        # ADDED: Generate the list of individual delegate votes
        # Sort by delegate name for consistent display
        vote_list_items = [
            f"- {delegate}: {vote.upper()}"
            for delegate, vote in sorted(current_vote_tally['voters'].items())
        ]
        # Include delegates who DID NOT vote (if any)
        voted_delegates = set(current_vote_tally['voters'].keys())
        not_voted_delegates = sorted([d for d in VALID_DELEGATES if d not in voted_delegates])

        if not_voted_delegates:
            vote_list_items.append("\n--- DELEGATES WHO DID NOT VOTE ---\n")
            vote_list_items.extend([f"- {delegate}: NOT CAST" for delegate in not_voted_delegates])

        vote_list_content = "\n".join(vote_list_items)

        # Create the result document
        result_content = (
            f"VOTE ON: {target_title}\n"
            f"--- FINAL RESULT ---\n"
            f"Result: {result} ({'Passed' if result == 'PASSED' else 'Failed'} by simple majority)\n\n"
            f"Yay Votes: {yay}\n"
            f"Nay Votes: {nay}\n"
            f"Abstain Votes: {abstain}\n"
            f"Total Votes Cast: {total_votes} out of {len(VALID_DELEGATES)} possible votes\n\n"
            f"--- INDIVIDUAL DELEGATE VOTES ---\n"
            f"{vote_list_content}\n"  # Insert the detailed vote list here
        )
        new_doc = {
            'id': str(uuid.uuid4()),
            'type': 'vote_result',
            'title': f'VOTE RESULT: {target_title}',
            'content': result_content,
            'delegate': 'CHAIRMAN',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        mun_documents.append(new_doc)

        # Reset the global vote state
        current_vote_target_id = None
        # CHANGED: Reset 'voters' to an empty dictionary
        current_vote_tally = {'yay': 0, 'nay': 0, 'abstain': 0, 'voters': {}}

        # Broadcast the new stream and inform clients the vote is over
        broadcast_stream()
        socketio.emit('vote_ended', broadcast=True)

        emit('feedback', {'message': f'Vote on "{new_doc["title"]}" finalized and published.'})
        emit('admin_state_update', {})  # Trigger an admin page refresh

    # --- Clear Stream Action ---
    elif action == 'clear_stream':
        mun_documents = []
        current_vote_target_id = None
        # CHANGED: Reset 'voters' to an empty dictionary
        current_vote_tally = {'yay': 0, 'nay': 0, 'abstain': 0, 'voters': {}}

        # End any active vote and update the stream
        socketio.emit('vote_ended', broadcast=True)
        broadcast_stream()
        emit('feedback', {'message': 'Document stream cleared.'})
        emit('admin_state_update', {})

    # --- Announcement Action ---
    elif action == 'announce':
        announcement_content = data.get('content', 'Chairman made an announcement.')
        new_doc = {
            'id': str(uuid.uuid4()),
            'type': 'moderator_announcement',
            'title': 'CHAIRMAN ANNOUNCEMENT',
            'content': announcement_content,
            'delegate': 'CHAIRMAN',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        mun_documents.append(new_doc)
        broadcast_stream()
        emit('feedback', {'message': 'Announcement posted to the stream.'})


# --- APPLICATION STARTUP ---

if __name__ == '__main__':
    # Use the eventlet WSGI server for robust SocketIO performance
    app.logger.setLevel('INFO')
    app.logger.info("Starting MUN app with eventlet server on port 5000...")
    eventlet.wsgi.server(eventlet.listen(('', 5000)), app, debug=True)
