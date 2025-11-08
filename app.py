from flask import Flask, render_template, request, redirect, url_for, session, flash, get_flashed_messages
from flask_socketio import SocketIO, emit
from datetime import datetime
import json

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
current_vote_target = None
current_vote_tally = {'yay': 0, 'nay': 0, 'voters': set()}


# --- UTILITY FUNCTIONS ---

def get_current_user_id():
    """Retrieves the current user ID from the session."""
    return session.get('user', 'Guest')


def render_stream():
    """Renders the current state of the document stream into HTML."""
    # This is simplified. In a real app, you'd use Jinja to render templates here.
    html_content = '<div class="space-y-4">'
    for doc in reversed(mun_documents):
        # Determine color/style based on type
        bg_class = 'bg-white border-gray-200'
        title_tag = 'h3'
        if doc['type'] == 'resolution':
            bg_class = 'bg-blue-50 border-blue-400'
        elif doc['type'] == 'amendment':
            bg_class = 'bg-yellow-50 border-yellow-400'
        elif doc['type'] == 'vote_result':
            bg_class = 'bg-green-100 border-green-600'

        html_content += f"""
        <div class="p-4 border-l-4 {bg_class} rounded-md shadow-md">
            <{title_tag} class="text-lg font-semibold text-gray-800">{doc['title']}</{title_tag}>
            <p class="text-sm text-gray-600 mt-1">
                <span class="font-medium text-gray-900">{doc['delegate']}</span> 
                ({doc['type'].replace('_', ' ').title()}) - 
                <span class="text-xs text-gray-500">{doc['timestamp']}</span>
            </p>
            <p class="mt-2 text-gray-700 whitespace-pre-wrap">{doc.get('content', '')}</p>
        </div>
        """
    html_content += '</div>'
    return html_content


def broadcast_stream():
    """Emits the updated stream to all connected clients."""
    stream_html = render_stream()
    # FIX 1: Ensure broadcast=True is used to hit all connected dashboards/admins
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
            return render_template('login.html')

    return render_template('login.html')


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
    return render_template('delegate.html', delegate_id=session['user'])


@app.route('/admin')
def admin_page():
    if session.get('role') != 'admin':
        flash('Access denied. Please log in as the Administrator.', 'error')
        return redirect(url_for('login'))
    return render_template('admin.html', delegate_id=session['user'])


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

    # Send current vote status on connect (New)
    global current_vote_target
    if current_vote_target:
        emit('vote_started', {'target': current_vote_target})


@socketio.on('mun_submission')
def handle_mun_submission(data):
    """Handles submissions of resolutions, amendments, and votes."""

    submission_type = data.get('type')
    delegate_id = session.get('user')

    if not delegate_id or delegate_id not in VALID_DELEGATES and delegate_id != ADMIN_USER:
        emit('feedback', {'message': 'Authentication error.'})
        return

    # --- Vote Submission Handler (FIX 2.A) ---
    if submission_type == 'vote':
        global current_vote_target, current_vote_tally
        vote = data.get('vote')

        if not current_vote_target:
            emit('feedback', {'message': 'No formal vote is currently active.'})
            return

        if delegate_id in current_vote_tally['voters']:
            emit('feedback', {'message': 'You have already cast your vote.'})
            return

        if vote in ['yay', 'nay']:
            current_vote_tally[vote] += 1
            current_vote_tally['voters'].add(delegate_id)
            emit('feedback', {'message': f'Vote recorded: {vote.upper()} on {current_vote_target}'})

            # Broadcast the live tally update to the admin page
            socketio.emit('vote_tally_update', {
                'target': current_vote_target,
                'yay': current_vote_tally['yay'],
                'nay': current_vote_tally['nay'],
                'voter_count': len(current_vote_tally['voters'])
            }, broadcast=True)
            return  # Stop here to prevent individual votes from showing in the main stream

        return

    # --- Resolution/Amendment Submission Handler ---
    elif submission_type in ['resolution', 'amendment']:
        new_doc = {
            'type': submission_type,
            'title': data.get('title'),
            'content': data.get('content'),
            'delegate': delegate_id,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        mun_documents.append(new_doc)

        emit('feedback', {'message': f'{submission_type.title()} "{new_doc["title"]}" submitted.'}, broadcast=False)

        # Broadcast the updated stream to all clients (FIX 1)
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
        global current_vote_target, current_vote_tally
        target_title = data.get('target', 'Unnamed Resolution/Amendment')

        # 1. Reset and activate the vote
        current_vote_target = target_title
        current_vote_tally = {'yay': 0, 'nay': 0, 'voters': set()}

        flash(f'Formal vote on "{target_title}" has started.', 'info')
        # 2. Announce the vote start to all clients (Delegates need this)
        socketio.emit('vote_started', {'target': target_title}, broadcast=True)
        # 3. Inform Admin directly
        emit('feedback', {'message': f'Formal vote on "{target_title}" started.'})

    elif action == 'finalize_vote':
        global current_vote_target, current_vote_tally

        if not current_vote_target:
            emit('feedback', {'message': 'No vote is currently active to finalize.'})
            return

        # 1. Calculate the result
        yay = current_vote_tally['yay']
        nay = current_vote_tally['nay']
        total_votes = yay + nay
        result = 'PASSED' if yay > nay else 'FAILED'

        # 2. Create the result document
        result_content = (
            f"Result: {result}\n\n"
            f"Yay: {yay}\n"
            f"Nay: {nay}\n"
            f"Abstentions/Not Voted: {len(VALID_DELEGATES) - len(current_vote_tally['voters'])}\n"
            f"Total Votes Cast: {total_votes}"
        )
        new_doc = {
            'type': 'vote_result',
            'title': f'VOTE RESULT: {current_vote_target}',
            'content': result_content,
            'delegate': 'CHAIRMAN',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        mun_documents.append(new_doc)

        # 3. Reset the global vote state
        current_vote_target = None
        current_vote_tally = {'yay': 0, 'nay': 0, 'voters': set()}

        # 4. Broadcast the new stream and inform clients the vote is over
        broadcast_stream()
        socketio.emit('vote_ended', broadcast=True)  # Tells delegates to hide buttons

        emit('feedback', {'message': f'Vote on "{new_doc["title"]}" finalized and published.'})

    elif action == 'clear_stream':
        global mun_documents
        mun_documents = []
        broadcast_stream()
        emit('feedback', {'message': 'Document stream cleared.'})


if __name__ == '__main__':
    # Use a high-level logging configuration
    app.logger.setLevel('INFO')
    socketio.run(app, debug=True)