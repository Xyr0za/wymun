import eventlet
# eventlet.monkey_patch() is often recommended for full async compatibility,
# but eventlet.wsgi.server and socketio(async_mode='eventlet') handles it well enough here.
# If you run into issues, uncommenting the next line might help.
# eventlet.monkey_patch()

from flask import Flask, render_template, request, redirect, url_for, session, flash, get_flashed_messages
from flask_socketio import SocketIO, emit
from datetime import datetime
import json
import logging
import uuid
import time
import threading  # Use threading for the simple sleep logic, or eventlet.spawn

# Set up basic logging (optional but helpful)
logging.basicConfig(level=logging.INFO)

# --- CONFIGURATION ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'A_VERY_SECRET_KEY_FOR_MUN_APP'
# Configure SocketIO explicitly for eventlet async mode for stability
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Hardcoded roles for simulation
ADMIN_USER = 'ADMIN'
VALID_DELEGATES = ['UK', 'FRANCE', 'USA', 'CHINA', 'RUSSIA', 'GERMANY', 'INDIA']  # Updated the original list

# --- GLOBAL STATE (Database/Firestore stand-in) ---
mun_documents = []
# UPDATED: Added 'abstain' to the tally
current_vote_tally = {'yay': 0, 'nay': 0, 'abstain': 0, 'voters': set()}
current_vote_target_id = None
# NEW: Variable to hold the greenlet/thread object for the auto-finalizer
vote_timer_thread = None
# NEW: The duration for the auto-finalize (30 seconds)
VOTE_DURATION_SECONDS = 30


# --- UTILITY FUNCTIONS ---

def get_current_user_id():
    """Retrieves the current user ID from the session."""
    return session.get('user', 'Guest')


def get_document_by_id(doc_id):
    """Retrieves a document from the stream by its ID."""
    return next((doc for doc in mun_documents if doc.get('id') == doc_id), None)


def render_stream():
    """Renders the current state of the document stream into HTML."""
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
            # --- MODIFICATION START ---
            # Check the content for the result status
            if 'Result: PASSED' in doc.get('content', ''):
                bg_class = 'bg-green-100 border-green-700'
            elif 'Result: FAILED' in doc.get('content', ''):
                # Apply red styling for failed votes
                bg_class = 'bg-red-100 border-red-700'
            else:
                # Default for vote_result if content is unexpected
                bg_class = 'bg-gray-100 border-gray-500'
            # --- MODIFICATION END ---
        elif doc['type'] == 'moderator_announcement':
            bg_class = 'bg-red-50 border-red-500'

        # Check if this document is the current vote target
        if globals().get('current_vote_target_id') == doc.get('id'):
            vote_status = '<span class="text-xs font-bold text-red-600 bg-red-100 px-2 py-0.5 rounded-full ml-2">VOTING ACTIVE</span>'

        doc_id_display = f'<span class="text-xs text-gray-400 ml-2">ID: {doc.get("id", "N/A")}</span>'

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
    Emits the updated stream to all connected clients.
    (This is kept for the delegate/admin pages, even if the dashboard uses polling)
    """
    stream_html = render_stream()
    socketio.emit('stream_update', {'data': stream_html}, broadcast=True)


# --- NEW: AUTO-FINALIZATION LOGIC ---

def auto_finalize_vote():
    """
    Spawns a greenlet/thread to wait for VOTE_DURATION_SECONDS and then finalize the vote.
    """
    # Use eventlet.sleep() instead of time.sleep() for non-blocking wait
    app.logger.info(f"Vote timer started for {VOTE_DURATION_SECONDS} seconds.")
    eventlet.sleep(VOTE_DURATION_SECONDS)
    app.logger.info("Vote duration expired. Auto-finalizing vote.")

    # Check if a vote is still active before finalizing
    if globals().get('current_vote_target_id'):
        # Call the actual finalization logic
        finalize_current_vote()
    else:
        app.logger.info("No vote active, skipping auto-finalization.")


def finalize_current_vote():
    """
    The core logic to finalize the vote, separated for reusability.
    This must be called within the Flask/SocketIO context (e.g., inside a handler or spawned greenlet).
    """
    # Use 'global' keyword to modify the global state variables
    global mun_documents, current_vote_target_id, current_vote_tally, vote_timer_thread

    target_doc = get_document_by_id(current_vote_target_id)
    if not current_vote_target_id or not target_doc:
        # Should not happen if called correctly, but for safety
        app.logger.warning("Attempted to finalize vote but no target ID was set.")
        return

    target_title = target_doc['title']

    # 1. Calculate the result
    yay = current_vote_tally['yay']
    nay = current_vote_tally['nay']
    abstain = current_vote_tally['abstain']
    total_votes = yay + nay + abstain
    # Result logic based on Yay vs Nay (Abstentions don't count towards the majority)
    result = 'PASSED' if yay > nay else 'FAILED'

    # 2. Create the result document
    result_content = (
        f"VOTE ON: {target_title}\n"
        f"--- FINAL RESULT (Auto-Closed after {VOTE_DURATION_SECONDS}s) ---\n"
        f"Result: {result} ({'Passed' if result == 'PASSED' else 'Failed'} by simple majority)\n\n"
        f"Yay Votes: {yay}\n"
        f"Nay Votes: {nay}\n"
        f"Abstain Votes: {abstain}\n"
        f"Total Votes Cast: {total_votes}\n"
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

    # 3. Reset the global vote state
    current_vote_target_id = None
    current_vote_tally = {'yay': 0, 'nay': 0, 'abstain': 0, 'voters': set()}
    vote_timer_thread = None  # Reset the timer variable

    # 4. Broadcast the new stream and inform clients the vote is over
    broadcast_stream()
    socketio.emit('vote_ended', broadcast=True)

    # The admin is informed via the socket emit inside the moderator_action handler,
    # but since this is auto-called, we must inform them here as well.
    # Note: socketio.emit outside of a request context requires a context wrapper,
    # but the way eventlet is used with Flask-SocketIO often handles this.
    socketio.emit('feedback', {'message': f'Vote on "{new_doc["title"]}" finalized automatically.'})
    socketio.emit('admin_state_update', {})  # For admin page to refresh


# --- ROUTES (Omitted for brevity, they are unchanged) ---
# ... (all routes remain the same) ...

@app.route('/')
def index():
    if 'user' in session:
        if session.get('role') == 'admin':
            return redirect(url_for('admin_page'))
        else:
            return redirect(url_for('delegate_page'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').upper().strip()

        if username == ADMIN_USER:
            session['user'] = username
            session['role'] = 'admin'
            flash(f'Logged in as Chairman ({username}).', 'success')
            return redirect(url_for('admin_page'))

        elif username in VALID_DELEGATES:
            session['user'] = username
            session['role'] = 'delegate'
            flash(f'Logged in as Delegate for {username}.', 'success')
            return redirect(url_for('delegate_page'))

        else:
            flash('Invalid Delegate ID or Admin code.', 'error')
            messages = [(msg, category) for msg, category in get_flashed_messages(with_categories=True)]
            return render_template('login.html', messages=messages)

    messages = [(msg, category) for msg, category in get_flashed_messages(with_categories=True)]
    return render_template('login.html', messages=messages)


@app.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('role', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


@app.route('/delegate')
def delegate_page():
    if session.get('role') != 'delegate':
        flash('Access denied. Please log in as a delegate.', 'error')
        return redirect(url_for('login'))

    messages = [(msg, category) for msg, category in get_flashed_messages(with_categories=True)]
    # The delegate.html template no longer needs 'stream_content'
    return render_template('delegate.html', delegate_id=session['user'], messages=messages)


@app.route('/admin')
def admin_page():
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
                           current_vote_target=current_target
                           )


@app.route('/dashboard')
def dashboard():
    # The stream_content variable is rendered initially by Flask on page load
    return render_template('dashboard.html', stream_content=render_stream())


# FIX 1: New route to serve just the stream HTML for AJAX polling
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
    """Handles new client connection."""
    user = get_current_user_id()
    app.logger.info(f'{user} connected.')

    # Send current vote status on connect
    global current_vote_target_id
    if current_vote_target_id:
        target_doc = get_document_by_id(current_vote_target_id)
        if target_doc:
            emit('vote_started', {'target': target_doc['title']})


@socketio.on('mun_submission')
def handle_mun_submission(data):
    # Use 'global' keyword to modify the global state variables
    global current_vote_target_id, current_vote_tally

    submission_type = data.get('type')
    delegate_id = session.get('user')

    if not delegate_id or delegate_id not in VALID_DELEGATES and delegate_id != ADMIN_USER:
        emit('feedback', {'message': 'Authentication error.'})
        return

    # --- Vote Submission Handler ---
    if submission_type == 'vote':
        vote = data.get('vote')
        target_doc = get_document_by_id(current_vote_target_id)

        if not current_vote_target_id or not target_doc:
            emit('feedback', {'message': 'No formal vote is currently active.'})
            return

        if delegate_id in current_vote_tally['voters']:
            emit('feedback', {'message': 'You have already cast your vote.'})
            return

        # UPDATED: Added 'abstain' to the valid vote options
        if vote in ['yay', 'nay', 'abstain']:
            current_vote_tally[vote] += 1
            current_vote_tally['voters'].add(delegate_id)
            emit('feedback', {'message': f'Vote recorded: {vote.upper()} on {target_doc["title"]}'})

            # Broadcast the live tally update to the admin page
            # 💡 MODIFICATION IS HERE: Simplify the payload for the client
            socketio.emit('vote_tally_update', {
                'target_id': current_vote_target_id,
                'target_title': target_doc['title'],
                # Removed 'yay' and 'nay' keys
                # 'yay': current_vote_tally['yay'],
                # 'nay': current_vote_tally['nay'],
                'voter_count': len(current_vote_tally['voters']),
                'total_delegates': len(VALID_DELEGATES)
            }, broadcast=True)
            return

        return

    # --- Resolution/Amendment Submission Handler ---
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

        # Removed broadcast_stream() here as the delegate no longer views the stream,
        # and the admin/dashboard stream updates are handled via separate calls or polling.
        # However, we must ensure admin/dashboard still sees the new document.
        broadcast_stream()  # Keep this to update Admin/Dashboard, but the delegate won't see it.
        return

    emit('feedback', {'message': 'Invalid submission type.'})


@socketio.on('moderator_action')
def handle_moderator_action(data):
    """Handles actions specific to the Admin (Chairman) role."""
    # Use 'global' keyword to modify the global state variables
    global mun_documents, current_vote_target_id, current_vote_tally, vote_timer_thread

    if session.get('role') != 'admin':
        emit('feedback', {'message': 'Unauthorized action.'})
        return

    action = data.get('action')

    if action == 'start_vote':
        target_doc_id = data.get('target_id')
        target_doc = get_document_by_id(target_doc_id)

        if not target_doc or target_doc['type'] not in ['resolution', 'amendment']:
            emit('feedback', {'message': 'Invalid document ID or document type for voting.'})
            return

        # Guard against starting a vote while another is active
        if current_vote_target_id:
            emit('feedback', {'message': 'Cannot start a new vote; one is already active.'})
            return

        # 1. Reset and activate the vote
        current_vote_target_id = target_doc_id
        current_vote_tally = {'yay': 0, 'nay': 0, 'abstain': 0, 'voters': set()}
        target_title = target_doc['title']

        # 2. Announce the vote start to all clients (Delegates need this)
        socketio.emit('vote_started', {'target': target_title}, broadcast=True)
        emit('feedback',
             {'message': f'Formal vote on "{target_title}" started. Auto-closing in {VOTE_DURATION_SECONDS} seconds.'})

        # 3. START THE AUTO-FINALIZATION TIMER
        # Use eventlet.spawn to run the auto_finalize_vote function concurrently
        vote_timer_thread = eventlet.spawn(auto_finalize_vote)

        # Add a record to the stream that a vote has started
        new_doc = {
            'id': str(uuid.uuid4()),
            'type': 'moderator_announcement',
            'title': 'VOTE STARTED',
            'content': f'A formal vote is now open on the **{target_doc["type"].upper()}**: {target_title}. All delegates must cast their vote (YAY/NAY/ABSTAIN). The vote will close automatically in {VOTE_DURATION_SECONDS} seconds.',
            'delegate': 'CHAIRMAN',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        mun_documents.append(new_doc)
        broadcast_stream()


    elif action == 'finalize_vote':

        if not current_vote_target_id:
            emit('feedback', {'message': 'No vote is currently active to finalize.'})
            return

        # Optional: Cancel the auto-finalize timer if the admin manually closes it early
        if vote_timer_thread:
            # eventlet.kill() is the method to stop a greenlet
            eventlet.kill(vote_timer_thread)
            vote_timer_thread = None
            app.logger.info("Admin manually closed the vote, auto-finalize timer cancelled.")

        # Call the core finalization logic
        finalize_current_vote()

        emit('feedback', {'message': 'Vote finalized and published.'})
        emit('admin_state_update', {})


    elif action == 'clear_stream':
        # Safely stop the auto-finalize timer if it's running
        if vote_timer_thread:
            eventlet.kill(vote_timer_thread)
            vote_timer_thread = None

        mun_documents = []
        current_vote_target_id = None
        # UPDATED to include 'abstain'
        current_vote_tally = {'yay': 0, 'nay': 0, 'abstain': 0, 'voters': set()}
        socketio.emit('vote_ended', broadcast=True)

        broadcast_stream()
        emit('feedback', {'message': 'Document stream cleared.'})
        emit('admin_state_update', {})


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


if __name__ == '__main__':
    # Use a high-level logging configuration
    app.logger.setLevel('INFO')

    # FIX: Run the application using eventlet WSGI server for robust SocketIO performance
    app.logger.info("Starting MUN app with eventlet server...")
    # This is how Gunicorn will run the app (ensure you run this if not using Gunicorn)
    eventlet.wsgi.server(eventlet.listen(('', 5000)), app, debug=True)