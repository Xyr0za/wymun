from flask import Flask, render_template, request, redirect, url_for, session, flash, get_flashed_messages
from flask_socketio import SocketIO, emit
from datetime import datetime
import json
import logging
import uuid  # Import for generating unique IDs

# Set up basic logging (optional but helpful)
logging.basicConfig(level=logging.INFO)

# --- CONFIGURATION ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'A_VERY_SECRET_KEY_FOR_MUN_APP'
socketio = SocketIO(app)

# Hardcoded roles for simulation
ADMIN_USER = 'ADMIN'
VALID_DELEGATES = ['UK', 'FRANCE', 'USA', 'CHINA', 'RUSSIA', 'GERMANY', 'INDIA']

# --- GLOBAL STATE (Database/Firestore stand-in) ---
# A list to store the chronological event stream
mun_documents = []

# Vote Tracking State (New)
current_vote_tally = {'yay': 0, 'nay': 0, 'voters': set()}
current_vote_target_id = None  # Tracks the ID of the document being voted on


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
    for doc in reversed(mun_documents):
        # Determine color/style based on type
        bg_class = 'bg-gray-50 border-gray-300'
        title_tag = 'h3'
        border_color = 'border-l-4'
        vote_status = ''  # New variable for vote status indicator

        if doc['type'] == 'resolution':
            bg_class = 'bg-blue-50 border-blue-500'
        elif doc['type'] == 'amendment':
            bg_class = 'bg-yellow-50 border-yellow-500'
        elif doc['type'] == 'vote_result':
            bg_class = 'bg-green-100 border-green-700'
        elif doc['type'] == 'moderator_announcement':
            bg_class = 'bg-red-50 border-red-500'

        # Add a visual indicator if this document is the current vote target
        if current_vote_target_id == doc.get('id'):
            vote_status = '<span class="text-xs font-bold text-red-600 bg-red-100 px-2 py-0.5 rounded-full ml-2">VOTING ACTIVE</span>'

        # Include the ID in the rendered content (hidden, but helpful for debugging/admin actions)
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
    """Emits the updated stream to all connected clients."""
    stream_html = render_stream()
    socketio.emit('stream_update', {'data': stream_html}, broadcast=True)


# --- ROUTES ---

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
            # Fetch flashed messages for rendering
            messages = [(msg, category) for msg, category in get_flashed_messages(with_categories=True)]
            return render_template('login.html', messages=messages)

    # Fetch flashed messages for GET request (e.g., redirected from index)
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
    return render_template('delegate.html', delegate_id=session['user'], messages=messages)


@app.route('/admin')
def admin_page():
    if session.get('role') != 'admin':
        flash('Access denied. Please log in as the Administrator.', 'error')
        return redirect(url_for('login'))

    messages = [(msg, category) for msg, category in get_flashed_messages(with_categories=True)]

    # Pass the list of votable documents to the admin template
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
    return render_template('dashboard.html', stream_content=render_stream())


# --- SOCKETIO EVENT HANDLERS ---

@socketio.on('connect')
def handle_connect():
    """Handles new client connection."""
    user = get_current_user_id()
    app.logger.info(f'{user} connected.')

    # Send the initial stream content immediately upon connection
    emit('stream_update', {'data': render_stream()})

    # Send current vote status on connect
    global current_vote_target_id
    if current_vote_target_id:
        target_doc = get_document_by_id(current_vote_target_id)
        if target_doc:
            emit('vote_started', {'target': target_doc['title']})


@socketio.on('mun_submission')
def handle_mun_submission(data):
    """Handles submissions of resolutions, amendments, and votes."""

    submission_type = data.get('type')
    delegate_id = session.get('user')

    if not delegate_id or delegate_id not in VALID_DELEGATES and delegate_id != ADMIN_USER:
        emit('feedback', {'message': 'Authentication error.'})
        return

    # --- Vote Submission Handler ---
    if submission_type == 'vote':
        global current_vote_target_id, current_vote_tally
        vote = data.get('vote')

        target_doc = get_document_by_id(current_vote_target_id)

        if not current_vote_target_id or not target_doc:
            emit('feedback', {'message': 'No formal vote is currently active.'})
            return

        if delegate_id in current_vote_tally['voters']:
            emit('feedback', {'message': 'You have already cast your vote.'})
            return

        if vote in ['yay', 'nay']:
            current_vote_tally[vote] += 1
            current_vote_tally['voters'].add(delegate_id)
            emit('feedback', {'message': f'Vote recorded: {vote.upper()} on {target_doc["title"]}'})

            # Broadcast the live tally update to the admin page
            socketio.emit('vote_tally_update', {
                'target_id': current_vote_target_id,
                'target_title': target_doc['title'],
                'yay': current_vote_tally['yay'],
                'nay': current_vote_tally['nay'],
                'voter_count': len(current_vote_tally['voters']),
                'total_delegates': len(VALID_DELEGATES)
            }, broadcast=True)
            return  # Stop here to prevent individual votes from showing in the main stream

        return

    # --- Resolution/Amendment Submission Handler ---
    elif submission_type in ['resolution', 'amendment']:
        new_doc = {
            'id': str(uuid.uuid4()),  # Assign a unique ID
            'type': submission_type,
            'title': data.get('title'),
            'content': data.get('content'),
            'delegate': delegate_id,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        mun_documents.append(new_doc)

        emit('feedback', {'message': f'{submission_type.title()} "{new_doc["title"]}" submitted.'}, broadcast=False)

        # Broadcast the updated stream to all clients
        broadcast_stream()
        return

    emit('feedback', {'message': 'Invalid submission type.'})


@socketio.on('moderator_action')
def handle_moderator_action(data):
    """Handles actions specific to the Admin (Chairman) role."""

    if session.get('role') != 'admin':
        emit('feedback', {'message': 'Unauthorized action.'})
        return

    action = data.get('action')

    if action == 'start_vote':
        global current_vote_target_id, current_vote_tally
        target_doc_id = data.get('target_id')
        target_doc = get_document_by_id(target_doc_id)

        if not target_doc or target_doc['type'] not in ['resolution', 'amendment']:
            emit('feedback', {'message': 'Invalid document ID or document type for voting.'})
            return

        # 1. Reset and activate the vote
        current_vote_target_id = target_doc_id
        current_vote_tally = {'yay': 0, 'nay': 0, 'voters': set()}
        target_title = target_doc['title']

        # 2. Announce the vote start to all clients (Delegates need this)
        socketio.emit('vote_started', {'target': target_title}, broadcast=True)
        # 3. Inform Admin directly
        emit('feedback', {'message': f'Formal vote on "{target_title}" started.'})

        # Add a record to the stream that a vote has started
        new_doc = {
            'id': str(uuid.uuid4()),
            'type': 'moderator_announcement',
            'title': 'VOTE STARTED',
            'content': f'A formal vote is now open on the **{target_doc["type"].upper()}**: {target_title}. All delegates must cast their vote (YAY/NAY).',
            'delegate': 'CHAIRMAN',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        mun_documents.append(new_doc)
        broadcast_stream()


    elif action == 'finalize_vote':
        global current_vote_target_id, current_vote_tally

        target_doc = get_document_by_id(current_vote_target_id)
        if not current_vote_target_id or not target_doc:
            emit('feedback', {'message': 'No vote is currently active to finalize.'})
            return

        target_title = target_doc['title']

        # 1. Calculate the result
        yay = current_vote_tally['yay']
        nay = current_vote_tally['nay']
        total_votes = yay + nay

        # Simple majority rule check (yay > nay)
        result = 'PASSED' if yay > nay else 'FAILED'

        # 2. Create the result document
        voters_list = sorted(list(current_vote_tally['voters']))

        result_content = (
            f"VOTE ON: {target_title}\n"
            f"--- FINAL RESULT ---\n"
            f"Result: {result} ({'Passed' if result == 'PASSED' else 'Failed'} by simple majority)\n\n"
            f"Yay Votes: {yay}\n"
            f"Nay Votes: {nay}\n"
            f"Total Votes Cast: {total_votes}\n"
            f"Delegates who Voted: {', '.join(voters_list)}\n"
            f"Delegates who did NOT Vote: {', '.join(sorted([d for d in VALID_DELEGATES if d not in current_vote_tally['voters']]))}"
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
        current_vote_tally = {'yay': 0, 'nay': 0, 'voters': set()}

        # 4. Broadcast the new stream and inform clients the vote is over
        broadcast_stream()
        socketio.emit('vote_ended', broadcast=True)  # Tells delegates to hide buttons

        emit('feedback', {'message': f'Vote on "{new_doc["title"]}" finalized and published.'})

        # Send an update to the admin page to refresh the votable list and clear tally display
        emit('admin_state_update', {})


    elif action == 'clear_stream':
        global mun_documents, current_vote_target_id, current_vote_tally
        mun_documents = []
        # Also clear vote state just in case
        current_vote_target_id = None
        current_vote_tally = {'yay': 0, 'nay': 0, 'voters': set()}
        socketio.emit('vote_ended', broadcast=True)

        broadcast_stream()
        emit('feedback', {'message': 'Document stream cleared.'})
        emit('admin_state_update', {})


    elif action == 'announce':
        # Handles general announcements from the Chair
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
    socketio.run(app, debug=True)