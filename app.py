from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room

# Use 'eventlet' or 'gevent' for production deployment on Render
# if you want to run multiple workers, though for a basic app,
# the default Werkzeug server with eventlet installed is a start.
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_super_secret_key'  # Important for sessions/security
socketio = SocketIO(app, cors_allowed_origins="*")  # Use cors_allowed_origins for testing

# Variable to hold the text to display on the dashboard
live_text = "Waiting for delegate input..."


# --- Routes ---

@app.route('/dashboard')
def dashboard():
    """Main dashboard page - displays live updates."""
    # Pass the current text to the template on initial load
    return render_template('dashboard.html', current_text=live_text)


@app.route('/delegate')
def delegate():
    """Delegate input page - sends the data."""
    return render_template('delegate.html')


# --- WebSocket Handlers ---

@socketio.on('connect')
def handle_connect():
    """Handler for new client connections."""
    print('Client connected:', request.sid)
    # Automatically send the current status to a newly connected dashboard
    emit('update_text', {'data': live_text}, room=request.sid)


@socketio.on('delegate_message')
def handle_delegate_message(data):
    """
    Handles a message event from the /delegate page (via WebSocket).
    data should contain a 'message' key.
    """
    global live_text
    new_message = data.get('message', 'No message provided')
    live_text = f"**Delegate Update**: {new_message}"

    print(f"Received WebSocket message: {new_message}")

    # Send the update to ALL connected clients (i.e., the dashboard page)
    # The client-side JS will listen for the 'update_text' event.
    emit('update_text', {'data': live_text}, broadcast=True)


# --- POST Request Alternative (Less Recommended for Live Update) ---

@app.route('/post-update', methods=['POST'])
def handle_post_update():
    """
    Handles a POST request from the /delegate page (as an alternative).
    The /delegate page would submit a form to this route.
    """
    global live_text
    new_message = request.form.get('message', 'No message provided via POST')
    live_text = f"**POST Update**: {new_message}"

    print(f"Received POST message: {new_message}")

    # With POST, you must STILL use SocketIO to broadcast the change
    # or the dashboard won't update automatically.
    socketio.emit('update_text', {'data': live_text}, broadcast=True)

    # Redirect back to the delegate page or to a confirmation page
    return "Message sent!", 200


# --- Server Start ---

if __name__ == '__main__':
    # Use socketio.run instead of app.run to include the WebSocket server
    socketio.run(app, debug=True)