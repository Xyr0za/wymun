import time
import uuid
from functools import wraps
from flask import Flask, render_template, request, session, redirect, url_for, flash, get_flashed_messages
from flask_socketio import SocketIO, emit, disconnect

# --- Configuration & Initialization ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_super_secret_mun_key'  # CHANGE THIS IN PRODUCTION
socketio = SocketIO(app)

# --- Global Data Store (In-memory for simplicity. Use Redis/DB for production scale) ---
# Each document in this list will be a dictionary.
mun_documents = []

# --- Authentication Helpers (Simple placeholder for MUN roles) ---

ADMIN_USER = 'ADMIN'


def requires_auth(role='delegate'):
    """Decorator to check user role for route access."""

    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user' not in session:
                flash("Please log in to access this page.", 'error')
                return redirect(url_for('login'))

            user_role = session.get('role')
            if role == 'admin' and user_role != 'admin':
                flash("Access denied. Admin privileges required.", 'error')
                return redirect(url_for('delegate'))  # Redirect to a safe page

            return f(*args, **kwargs)

        return decorated_function

    return decorator


# --- Data Rendering and Broadcasting ---

def render_mun_document(doc):
    """
    Renders a single document dictionary into a stylized HTML block.
    This function simulates the Jinja2 template rendering for clarity.
    In a real app, this would use render_template_string or be moved to a macro.
    """
    doc_id = doc.get('id', 'unknown')
    delegate = doc['delegate_id']
    timestamp = doc['timestamp']

    if doc['type'] == 'resolution':
        return f"""
        <div class="p-4 rounded-xl bg-indigo-100 border-l-4 border-indigo-600 shadow-sm relative">
            <h3 class="text-xl font-bold text-indigo-800 mb-1">📜 Resolution: {doc['title']}</h3>
            <p class="text-sm text-gray-600 mb-3">Submitted by: <span class="font-medium">{delegate}</span> at {timestamp}</p>
            <div class="prose max-w-none text-gray-700 bg-indigo-50 p-3 rounded">{doc['content'].replace('\n', '<br>')}</div>
            <button onclick="deleteDocument('{doc_id}')" class="absolute top-2 right-2 text-red-500 hover:text-red-700 text-sm font-bold">X Delete</button>
        </div>
        """
    elif doc['type'] == 'amendment':
        return f"""
        <div class="p-4 rounded-xl bg-orange-100 border-l-4 border-orange-600 shadow-sm relative">
            <h3 class="text-xl font-bold text-orange-800 mb-1">✏️ Amendment: {doc['target']}</h3>
            <p class="text-sm text-gray-600 mb-3">Proposed by: <span class="font-medium">{delegate}</span> at {timestamp}</p>
            <p class="text-gray-700 bg-orange-50 p-3 rounded">{doc['text']}</p>
            <button onclick="deleteDocument('{doc_id}')" class="absolute top-2 right-2 text-red-500 hover:text-red-700 text-sm font-bold">X Delete</button>
        </div>
        """
    elif doc['type'] == 'vote_result':
        return f"""
        <div class="p-4 rounded-xl bg-gray-200 border-l-4 border-gray-800 shadow-md text-center">
            <h3 class="text-2xl font-extrabold text-gray-900 mb-1">🏛️ FINAL VOTE RESULT 🏛️</h3>
            <p class="text-lg font-semibold text-gray-700">{doc['target']}</p>
            <div class="flex justify-around mt-3">
                <span class="text-2xl font-bold text-green-600">✅ YAY: {doc['yay_count']}</span>
                <span class="text-2xl font-bold text-red-600">❌ NAY: {doc['nay_count']}</span>
            </div>
            <button onclick="deleteDocument('{doc_id}')" class="absolute top-2 right-2 text-red-500 hover:text-red-700 text-sm font-bold">X Delete</button>
        </div>
        """
    elif doc['type'] == 'vote':
        # Votes are ephemeral and usually tallied, not displayed as individual documents.
        # For this admin view, we'll return an empty string or a special placeholder.
        return ""

    return ""  # Fallback


def render_stream(is_admin=False):
    """Generates the full HTML stream from the mun_documents list."""
    # We reverse to show newest documents at the top
    html_content = [render_mun_document(doc) for doc in reversed(mun_documents)]

    # Remove empty vote submissions from the displayed stream
    clean_content = [c for c in html_content if c.strip()]

    if not clean_content:
        return '<div class="text-center py-10 text-gray-500">No documents submitted yet.</div>'

    return "\n".join(clean_content)


def broadcast_stream():
    """Emits the updated stream to all connected clients."""
    # The rendered stream is the same for delegate and dashboard view.
    stream_html = render_stream()
    socketio.emit('stream_update', {'data': stream_html}, broadcast=True)


# --- HTTP Routes ---

@app.route('/', defaults={'path': 'login'})
@app.route('/<path:path>')
def redirect_to_login(path):
    if 'user' not in session:
        return redirect(url_for('login'))
    if session.get('role') == 'admin':
        return redirect(url_for('admin'))
    return redirect(url_for('delegate'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].upper().strip()

        if username == ADMIN_USER:
            session['user'] = ADMIN_USER
            session['role'] = 'admin'
            flash(f"Welcome, Chairman {ADMIN_USER}!", 'info')
            return redirect(url_for('admin'))

        if username:
            session['user'] = username
            session['role'] = 'delegate'
            flash(f"Logged in as Delegate: {username}", 'info')
            return redirect(url_for('delegate'))

        flash("Username cannot be empty.", 'error')

    # Get flashed messages before rendering the template
    messages = get_flashed_messages(with_categories=True)
    return render_template('login.html', messages=messages)


@app.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('role', None)
    flash("You have been logged out.", 'info')
    return redirect(url_for('login'))


@app.route('/delegate')
@requires_auth(role='delegate')
def delegate():
    # 'delegate_id' in template is used for JS client to identify itself
    return render_template('delegate.html', delegate_id=session['user'])


@app.route('/admin')
@requires_auth(role='admin')
def admin():
    # Admin view loads the stream initially
    initial_content = render_stream(is_admin=True)
    return render_template('admin.html', initial_content=initial_content)


@app.route('/dashboard')
def dashboard():
    # Public view loads the stream initially
    initial_content = render_stream(is_admin=False)
    return render_template('dashboard.html', initial_content=initial_content)


@app.route('/clear_stream', methods=['POST'])
@requires_auth(role='admin')
def clear_stream_route():
    """HTTP route for the admin's hard reset button."""
    global mun_documents
    mun_documents = []  # Hard reset the data

    # Broadcast the empty stream immediately
    broadcast_stream()

    flash("All MUN documents have been cleared (Hard Reset).", 'success')
    return redirect(url_for('admin'))


# --- SocketIO Event Handlers ---

# Variable to hold live vote tallies
current_vote_tally = {'yay': 0, 'nay': 0, 'voters': set()}
current_vote_target = None  # What is being voted on


@socketio.on('connect')
def handle_connect():
    # Ensures only authenticated users connect to the SocketIO
    if 'user' not in session:
        return False  # Reject connection if not authenticated

    print(f"{session.get('role')} {session['user']} connected.")

    # Immediately send the full stream to the newly connected client
    emit('stream_update', {'data': render_stream()})


@socketio.on('mun_submission')
def handle_mun_submission(data):
    """Handles resolution, amendment, and individual vote submissions."""
    delegate_id = session.get('user')
    submission_type = data.get('type')

    if not delegate_id:
        disconnect()
        return

    new_doc = {
        'id': str(uuid.uuid4()),
        'delegate_id': delegate_id,
        'type': submission_type,
        'timestamp': time.strftime("%H:%M:%S")
    }

    if submission_type == 'resolution':
        new_doc['title'] = data.get('title', 'Untitled Resolution')
        new_doc['content'] = data.get('content', 'No content provided.')
        mun_documents.append(new_doc)

    elif submission_type == 'amendment':
        new_doc['target'] = data.get('target', 'Unknown Target')
        new_doc['text'] = data.get('text', 'No amendment text provided.')
        mun_documents.append(new_doc)

    elif submission_type == 'vote':
        vote = data.get('vote')
        # Only process votes if a formal vote is currently active
        if current_vote_target and delegate_id not in current_vote_tally['voters']:
            if vote == 'yay':
                current_vote_tally['yay'] += 1
                current_vote_tally['voters'].add(delegate_id)
            elif vote == 'nay':
                current_vote_tally['nay'] += 1
                current_vote_tally['voters'].add(delegate_id)

            # Send a specific update to the admin only, showing the tally changing
            emit('vote_tally_update', current_vote_tally, room=ADMIN_USER)  # Assuming admin is in a room
            return  # Do not broadcast full stream for every vote
        else:
            # Optionally send feedback to the specific delegate if vote is inactive/already cast
            emit('feedback', {'message': 'Vote inactive or already cast.'})
            return  # Do not broadcast full stream

    # For Resolutions and Amendments, broadcast the updated stream
    if submission_type in ['resolution', 'amendment']:
        broadcast_stream()


@socketio.on('moderator_action')
@requires_auth(role='admin')  # Ensure only authenticated admins can send this
def handle_moderator_action(data):
    """Handles admin actions like deleting documents and managing votes."""
    global mun_documents, current_vote_tally, current_vote_target
    action = data.get('action')

    if action == 'delete':
        doc_id = data.get('document_id')

        # Remove the document from the global list
        global mun_documents
        mun_documents = [d for d in mun_documents if d.get('id') != doc_id]

        # Re-render and broadcast
        broadcast_stream()

    elif action == 'initiate_vote':
        target = data.get('target', 'Current Motion')

        # Reset tallies and set target
        current_vote_tally = {'yay': 0, 'nay': 0, 'voters': set()}
        current_vote_target = target

        # Announce the start of the vote to all delegates
        socketio.emit('vote_status', {'active': True, 'target': target}, broadcast=True)

    elif action == 'finalize_vote':
        target = current_vote_target
        yay = current_vote_tally['yay']
        nay = current_vote_tally['nay']

        # Create a final vote result document
        result_doc = {
            'id': str(uuid.uuid4()),
            'delegate_id': 'CHAIR',
            'type': 'vote_result',
            'timestamp': time.strftime("%H:%M:%S"),
            'target': target,
            'yay_count': yay,
            'nay_count': nay
        }

        # Add the result to the main document stream
        mun_documents.append(result_doc)

        # Deactivate the current vote
        current_vote_tally = {'yay': 0, 'nay': 0, 'voters': set()}
        current_vote_target = None

        # Announce the end of the vote and broadcast the result document
        socketio.emit('vote_status', {'active': False}, broadcast=True)
        broadcast_stream()


# --- Run Server ---

if __name__ == '__main__':
    # Use the dashboard template name for the public view
    app.add_url_rule('/stream', view_func=dashboard)
    socketio.run(app, debug=True)