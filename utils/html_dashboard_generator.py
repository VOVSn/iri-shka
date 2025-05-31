# utils/html_dashboard_generator.py

import datetime
from datetime import timezone, timedelta # Correct import
import config # For TIMEZONE_OFFSET_HOURS

# from logger import get_logger
# logger = get_logger("Iri-shka_App.utils.HTMLDashboardGenerator") # Uncomment if logging is needed here


HTML_DASHBOARD_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Iri-shka Status Dashboard</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol"; margin: 0; padding: 10px; background-color: #f0f2f5; color: #1c1e21; }}
        .container {{ max-width: 750px; margin: 15px auto; background-color: #ffffff; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1), 0 8px 16px rgba(0,0,0,0.1); }}
        h1 {{ color: #1877f2; text-align: center; font-size: 1.8em; margin-bottom: 5px; }}
        h2 {{ color: #333; border-bottom: 2px solid #1877f2; padding-bottom: 8px; margin-top: 25px; font-size: 1.4em;}}
        .header p {{ text-align: center; margin-top:0; color: #606770;}}
        .timestamp {{ font-size: 0.85em; color: #606770; text-align: right; margin-bottom: 15px; }}
        .section {{ margin-bottom: 25px; }}
        .status-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); gap: 12px; margin-bottom:15px; }}
        .status-box {{ padding: 12px 8px; border-radius: 6px; text-align: center; font-weight: 500; color: white; font-size: 0.9em; box-shadow: 0 1px 2px rgba(0,0,0,0.05);}}
        .status-ok, .status-active {{ background-color: #42b72a; }} /* Green for OK and Active states */
        .status-warn {{ background-color: #ffc107; color: #333;}} /* Amber */
        .status-error {{ background-color: #f02849; }} /* Red */
        .status-off, .status-disabled {{ background-color: #adb5bd; }} /* Grey for OFF and Disabled states */
        .status-info {{ background-color: #1877f2; }} /* Blue */
        .app-overall-status {{ font-weight: bold; font-size: 1.1em; padding: 10px; background-color: #e7f3ff; border-left: 4px solid #1877f2; margin-top: 10px; border-radius: 4px;}}
        ul {{ list-style-type: none; padding-left: 0; margin-top: 5px; }}
        li {{ background-color: #f0f2f5; margin-bottom: 6px; padding: 10px; border-radius: 4px; font-size: 0.95em; border: 1px solid #ddd; }}
        li strong {{ color: #1877f2; }}
        .kanban-section {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px;}}
        .kanban-column h3 {{ margin-top: 0; font-size: 1.1em; color: #4b4f56; padding-bottom: 5px; border-bottom: 1px solid #ccd0d5; }}
        .chat-turn strong {{ display: block; margin-bottom: 3px; color: #333;}}
        .chat-turn .admin {{ color: #1877f2; }}
        .chat-turn .irishka {{ color: #42b72a; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Iri-shka AI Assistant</h1>
            <p>Status Dashboard</p>
            <p class="timestamp">Generated: {generation_timestamp}</p>
        </div>

        <div class="section">
            <h2>Component Status</h2>
            <div class="status-grid">
                <div class="status-box {act_status_class}">{act_status_text}</div>
                <div class="status-box {inet_status_class}">{inet_status_text}</div>
                <div class="status-box {webui_status_class}">{webui_status_text}</div>
                <div class="status-box {tele_status_class}">{tele_status_text}</div>
                <div class="status-box {mem_status_class}">{mem_status_text}</div>
                <div class="status-box {hear_status_class}">{hear_status_text}</div>
                <div class="status-box {voice_status_class}">{voice_status_text}</div>
                <div class="status-box {mind_status_class}">{mind_status_text}</div>
                <div class="status-box {vis_status_class}">{vis_status_text}</div>
                <div class="status-box {art_status_class}">{art_status_text}</div>
            </div>
            <div class="app-overall-status"><strong>Overall App Status:</strong> {app_overall_status}</div>
        </div>

        <div class="section">
            <h2>Admin's Info ({admin_name})</h2>
            <div>
                <h3>Next Calendar Events</h3>
                <ul>{admin_calendar_events_html}</ul>
            </div>
            <!-- Admin Todos Removed -->
        </div>

        <div class="section">
            <h2>Iri-shka's Internal Tasks</h2>
            <div class="kanban-section">
                <div class="kanban-column">
                    <h3>Pending Tasks</h3>
                    <ul>{assistant_kanban_pending_html}</ul>
                </div>
                <!-- In Process Column Removed -->
                <div class="kanban-column">
                    <h3>Completed Tasks (Recent)</h3>
                    <ul>{assistant_kanban_completed_html}</ul>
                </div>
            </div>
        </div>
        
        <div class="section">
            <h2>Recent Admin Chat</h2>
            <ul>{admin_recent_chat_html}</ul>
        </div>

    </div>
</body>
</html>
"""

def get_status_css_class(status_type_str: str) -> str:
    """ Maps a status type string (e.g., 'ready', 'error') to a CSS class. """
    status_type_str = status_type_str.lower() # Normalize
    if status_type_str in ["ready", "polling", "loaded", "saved", "fresh", "idle", "ok_gpu", "active", "healthy"]: # Added "active" and "healthy" here
        return "status-ok" # This will make "active" green due to CSS rule for .status-ok
    elif status_type_str in ["loading", "checking", "pinging", "thinking", "busy"]:
        return "status-warn"
    elif status_type_str in ["error", "na", "timeout", "conn_error", "http_502", "http_other", "initfail", "unreachable", "bad_token", "net_error", "err", "ssl_err", "unhealthy", "no-conn", "err-chk"]:
        return "status-error"
    elif status_type_str in ["off", "disabled"]: # Grouped off and disabled (e.g. WebUI PAUSED)
        return "status-off" # This will make "disabled" grey due to CSS rule for .status-off
    return "status-info" # Default for unknown or other types like "n/a"

def generate_dashboard_html(
    admin_user_state: dict,
    assistant_state_snapshot: dict,
    admin_chat_history: list,
    component_statuses: dict,
    app_overall_status: str
) -> str:
    """Generates the HTML dashboard string."""
    
    try:
        tz_info = timezone(timedelta(hours=config.TIMEZONE_OFFSET_HOURS))
        generation_ts = datetime.datetime.now(tz_info).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception as e_ts:
        generation_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC") + f" (Error getting local tz: {e_ts})"


    def format_list_to_html(items_list, max_items=5, empty_message="<li>None</li>"):
        if not items_list: return empty_message
        return "".join([f"<li>{str(item)[:150].replace('<','<').replace('>','>')}</li>" for item in items_list[:max_items]])

    def format_calendar_events_to_html(events, max_items=5):
        if not events: return "<li>No upcoming events</li>"
        html_items = []
        try:
            # Ensure all items are dicts before trying to sort
            valid_events = [e for e in events if isinstance(e, dict)]
            sorted_events = sorted(
                valid_events, 
                key=lambda x: (
                    str(x.get("date", "9999-99-99")), 
                    str(x.get("time", "99:99"))
                )
            )
        except TypeError: 
            # Fallback if items are not dicts or sorting fails for other reasons
            sorted_events = [e for e in events if isinstance(e, dict)] # just filter, don't sort
        
        for event in sorted_events[:max_items]:
            desc = str(event.get("description") or event.get("name") or "Event")[:100].replace('<','<').replace('>','>')
            date_str = str(event.get("date", ""))
            time_str = str(event.get("time", ""))
            html_items.append(f"<li><strong>{desc}</strong><br><small>{date_str} {time_str}</small></li>")
        return "".join(html_items)
        
    def format_chat_history_to_html(history, max_turns=3):
        if not history: return "<li>No recent messages</li>"
        html_items = []
        for turn in history[-max_turns:]: 
            user_msg = str(turn.get("user", ""))[:150].replace('<','<').replace('>','>')
            asst_msg = str(turn.get("assistant", ""))[:150].replace('<','<').replace('>','>')
            item_html = "<li class='chat-turn'>"
            if user_msg:
                item_html += f"<strong class='admin'>Admin:</strong> {user_msg}<br>"
            if asst_msg:
                item_html += f"<strong class='irishka'>Iri-shka:</strong> {asst_msg}"
            item_html += "</li>"
            html_items.append(item_html)
        return "".join(html_items)

    assistant_tasks_from_state = assistant_state_snapshot.get("internal_tasks", {})
    if not isinstance(assistant_tasks_from_state, dict): 
        assistant_tasks_for_html = {"pending": [], "completed": []}
    else:
        assistant_tasks_for_html = {
            "pending": assistant_tasks_from_state.get("pending", []),
            "completed": assistant_tasks_from_state.get("completed", [])
        }

    
    if not isinstance(component_statuses, dict): component_statuses = {}

    def get_comp_status_text_and_type(key, default_prefix):
        status_tuple = component_statuses.get(key, (f"{default_prefix}: N/A", "unknown"))
        text = str(status_tuple[0]) if isinstance(status_tuple, (list, tuple)) and len(status_tuple) > 0 else f"{default_prefix}: N/A"
        type_str = str(status_tuple[1]) if isinstance(status_tuple, (list, tuple)) and len(status_tuple) > 1 else "unknown"
        return text, type_str

    comp_status_data = {}
    for key, prefix in [
        ("act", "ACT"), ("inet", "INET"), ("webui", "WEBUI"), ("tele", "TELE"),
        ("mem", "MEM"), ("hear", "HEAR"), ("voice", "VOICE"), ("mind", "MIND"),
        ("vis", "VIS"), ("art", "ART")
    ]:
        text, type_str = get_comp_status_text_and_type(key, prefix)
        comp_status_data[f"{key}_status_text"] = text.replace('<','<').replace('>','>')
        comp_status_data[f"{key}_status_class"] = get_status_css_class(type_str)


    template_data = {
        "generation_timestamp": generation_ts,
        "admin_name": str(admin_user_state.get("name", "Admin")).replace('<','<').replace('>','>'),
        
        **comp_status_data,

        "app_overall_status": str(app_overall_status).replace('<','<').replace('>','>'),
        
        "admin_calendar_events_html": format_calendar_events_to_html(admin_user_state.get("calendar_events", [])),
        
        "assistant_kanban_pending_html": format_list_to_html(assistant_tasks_for_html.get("pending", [])),
        "assistant_kanban_completed_html": format_list_to_html(assistant_tasks_for_html.get("completed", [])[-5:], empty_message="<li>No recent completed tasks</li>"),
        
        "admin_recent_chat_html": format_chat_history_to_html(admin_chat_history)
    }
    
    try:
        return HTML_DASHBOARD_TEMPLATE.format(**template_data)
    except KeyError as ke:
        # logger.error(f"KeyError during HTML dashboard formatting: {ke}. Available keys: {template_data.keys()}")
        return f"<html><body>Error formatting dashboard: Missing key {ke}. Please check template.</body></html>"
    except Exception as e_format_html:
        # logger.error(f"Unexpected error during HTML dashboard formatting: {e_format_html}", exc_info=True)
        return f"<html><body>Unexpected error formatting dashboard: {str(e_format_html).replace('<','<').replace('>','>')}.</body></html>"