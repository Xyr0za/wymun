
## API Reference

The application exposes both **traditional Flask routes** (for navigation, authentication, and polling) and **SocketIO event handlers** (for real-time communication).

### Flask Routes (HTTP Endpoints)

| Method | Path | Role | Description |
| :--- | :--- | :--- | :--- |
| **GET** | `/` | All | Redirects to `/login` or the appropriate main page (`/delegate` or `/admin`). |
| **GET, POST** | `/login` | All | Handles delegate/admin authentication. Sets `session['user']` and `session['role']`. |
| **GET** | `/logout` | All | Clears session and redirects to `/login`. |
| **GET** | `/roster` | All | Displays the delegate roster (`VALID_DELEGATES`). |
| **GET** | `/delegate` | Delegate | Delegate's main interface. Requires `role == 'delegate'`. |
| **GET** | `/admin` | Admin | Chairman/Admin interface for managing submissions and voting. Requires `role == 'admin'`. |
| **GET** | `/dashboard` | Public | Publicly visible read-only view of the document stream and vote monitor. |
| **GET** | `/stream_content_api` | Public/Polling | Returns the raw HTML of the document stream for AJAX polling. |
| **GET** | `/vote_status_api` | Public/Polling | Returns JSON data on the current vote status, tally, and if the current user has voted. |

#### `/vote_status_api` Response Format (JSON)

```json
{
  "active": true, // Boolean: Is a vote currently active?
  "target_title": "Resolution on Climate Change", // Title of the document being voted on
  "tally": {
    "yay": 5,
    "nay": 2,
    "abstain": 1,
    "voter_count": 8, // Number of delegates who have voted
    "total_delegates": 10 // Total number of possible voters
  },
  "voted": true // Boolean: Has the current user/delegate cast a vote?
}
```

-----

### SocketIO Events

SocketIO is the primary mechanism for real-time updates and actions.

#### 1\. Incoming Events (Client $\rightarrow$ Server)

| Event Name | Role | Data Payload (JSON) | Description |
| :--- | :--- | :--- | :--- |
| `connect` | All | None | Triggered on client connection. Server responds with `vote_started` if a vote is active. |
| `mun_submission` | Delegate | See below | Used for submitting resolutions, amendments, and votes. |
| `moderator_action` | Admin | See below | Used by the Chairman to start/finalize votes, clear the stream, or make announcements. |

##### `mun_submission` Payloads:

| Type | Delegate/Admin | Fields | Example |
| :--- | :--- | :--- | :--- |
| `resolution` | Delegate | `type`, `title`, `content` | `{"type": "resolution", "title": "...", "content": "..."}` |
| `amendment` | Delegate | `type`, `title`, `content` | `{"type": "amendment", "title": "...", "content": "..."}` |
| `vote` | Delegate | `type`, `vote` (yay/nay/abstain) | `{"type": "vote", "vote": "yay"}` |

##### `moderator_action` Payloads:

| Action | Fields | Description |
| :--- | :--- | :--- |
| `start_vote` | `action`, `target_id` (document ID) | Starts a formal vote on the specified document. |
| `finalize_vote` | `action` | Closes the current vote, calculates results, and posts a `vote_result` document. |
| `clear_stream` | `action` | Clears all documents, resets the vote state, and ends any active vote. |
| `announce` | `action`, `content` | Posts a `moderator_announcement` to the stream. |

-----

#### 2\. Outgoing Events (Server $\rightarrow$ Client)

| Event Name | Role | Data Payload (JSON) | Description |
| :--- | :--- | :--- | :--- |
| `stream_update` | All | `{'data': html_content}` | **Primary update event.** Broadcasts the latest HTML rendering of `mun_documents` to update the stream/dashboard. |
| `vote_started` | All | `{'target': target_title}` | Notifies clients that a new formal vote has begun. Triggers vote UI on delegate pages. |
| `vote_ended` | All | None | Notifies clients that the active vote has ended. Hides vote UI on delegate pages. |
| `vote_tally_update` | Admin | `{'voter_count', 'total_delegates', 'target_id', 'target_title'}` | Provides an incremental update on the number of delegates who have voted. (Used mainly for admin monitoring). |
| `feedback` | Sender | `{'message': string}` | Sends a simple feedback message to the client who initiated the action (e.g., success/error message). |
| `admin_state_update`| Admin | None | Signals the admin page to refresh its state (e.g., after finalizing a vote). |

-----

## README: MUN Live Stream & Voting System

### Overview

This project is a real-time web application designed to manage the flow of documents and voting during a **Model United Nations (MUN)** conference. It uses **Flask** as the web framework and **Flask-SocketIO** to enable instant updates for all connected clients, simulating a live document stream and vote monitor.

### Key Features

  * **User Roles:** **Delegate** (submission, voting) and **Chairman/Admin** (submission management, vote control).
  * **Live Document Stream:** Instantly broadcast resolutions, amendments, and announcements to all users and the public dashboard.
  * **Formal Voting System:**
      * Chairman can **Start** and **Finalize** a formal vote on any Resolution or Amendment.
      * Delegates can cast a single **YAY**, **NAY**, or **ABSTAIN** vote per formal motion.
      * Real-time monitoring of votes cast on the delegate and admin pages.
      * Final results (including a detailed breakdown of individual delegate votes) are posted back to the document stream.
  * **Stateless by Design (In-memory):** All documents (`mun_documents`) and the vote state (`current_vote_tally`) are held in memory. *A database is recommended for production use.*
  * **Scalability:** Uses `eventlet` with SocketIO, which is suitable for high-concurrency, real-time environments.

### System Components

1.  **Authentication (`/login`):** Validates users against a list of hardcoded or `delegates.json` defined names (`VALID_DELEGATES`) or the hardcoded `ADMIN_USER`.
2.  **Delegate Interface (`/delegate`):** Allows delegates to submit new documents and cast votes when a formal vote is active.
3.  **Admin Interface (`/admin`):** Provides the Chairman with tools to:
      * View all submissions.
      * Select a document and **start a formal vote**.
      * **Finalize the vote** and post the result.
      * Post **announcements**.
      * **Clear the entire document stream**.
4.  **Dashboard (`/dashboard`):** A public, read-only view of the live stream and the current vote status.

### Setup and Running

#### 1\. Prerequisites

You must have **Python 3.x** installed.

#### 2\. Installation

```bash
# Clone the repository (if applicable)
# git clone <repository_url>
# cd <project_directory>

# Install the necessary dependencies
pip install Flask Flask-SocketIO eventlet
```

#### 3\. Configuration

  * **Secret Key:** Change `app.config['SECRET_KEY'] = 'secret_key'` to a secure, long, random key in production.

  * **Delegates:** Create a file named **`delegates.json`** in the root directory:

    ```json
    {
        "Delegates": {
            "FRANCE": "French Republic",
            "CHINA": "People's Republic of China",
            "CANADA": "Dominion of Canada"
            // ... add all valid delegate names (keys will be uppercased)
        }
    }
    ```

    If the file is missing, the application defaults to a small list for testing.

#### 4\. Running the Application

The application is configured to use the `eventlet` server for asynchronous SocketIO handling.

```bash
python your_script_name.py
```

*The application will start on `http://127.0.0.1:5000`.*

### Usage Flow

1.  **Login:** Access `http://127.0.0.1:5000/login`.
      * Use one of the delegate names (e.g., `FRANCE`) to log in as a **Delegate**.
      * Use the admin code (e.g., `ADMIN`) to log in as the **Chairman**.
2.  **Delegate Submission:** Delegates submit **Resolutions** or **Amendments** from the `/delegate` page. These appear immediately on the `/dashboard` and `/admin` pages.
3.  **Chairman Action:** From the `/admin` page, the Chairman selects a submitted document and clicks **"Start Formal Vote"**.
4.  **Delegate Voting:** The formal vote UI appears instantly on the `/delegate` pages. Delegates cast their one vote (`yay`/`nay`/`abstain`).
5.  **Finalization:** When voting is complete, the Chairman clicks **"Finalize Vote"** on the `/admin` page. The result, including the tally and delegate breakdown, is posted to the stream, and the vote UI is removed from all delegate pages.

### Data Model (`mun_documents`)

Each item in the document stream is a dictionary with the following structure:

```json
{
    "id": "uuid4_string",
    "type": "resolution" | "amendment" | "vote_result" | "moderator_announcement",
    "title": "Document Title",
    "content": "The main text of the document or result details.",
    "delegate": "FRANCE" | "CHAIRMAN",
    "timestamp": "2025-11-19 11:00:45"
}
```