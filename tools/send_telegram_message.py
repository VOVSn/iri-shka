# tools/send_telegram_message_tool.py
import asyncio

# This tool relies on components passed via app_context,
# specifically the telegram_bot_handler_instance_ref and its async_loop.

async def execute(params: dict, app_context: dict, logger) -> dict:
    """
    Executes the action of sending a textual message to a specified Telegram user ID.
    """
    logger.info(f"Executing send_telegram_message tool with params: {params}")

    recipient_user_id_str = params.get('recipient_user_id')
    message_content = params.get('message_content')

    # --- Parameter Validation ---
    if not recipient_user_id_str:
        logger.error("Missing required parameter: 'recipient_user_id'")
        return {'status': 'error', 'message': "Error: Missing required parameter 'recipient_user_id'."}
    
    try:
        recipient_user_id = int(recipient_user_id_str)
    except ValueError:
        logger.error(f"Invalid 'recipient_user_id': '{recipient_user_id_str}'. Must be an integer.")
        return {'status': 'error', 'message': f"Error: Invalid 'recipient_user_id' format. Expected an integer, got '{recipient_user_id_str}'."}

    if not message_content or not isinstance(message_content, str) or not message_content.strip():
        logger.error("Missing or empty required parameter: 'message_content'")
        return {'status': 'error', 'message': "Error: Missing or empty required parameter 'message_content'. Message cannot be blank."}

    # --- Get Telegram Handler from app_context ---
    telegram_handler = app_context.get('telegram_bot_handler_instance_ref')
    if not telegram_handler:
        logger.error("TelegramBotHandler instance not found in app_context.")
        return {'status': 'error', 'message': "Error: Telegram integration is not available to send the message."}
    
    if not telegram_handler.async_loop or not telegram_handler.async_loop.is_running():
        logger.error("Telegram handler's asyncio loop is not available or not running.")
        return {'status': 'error', 'message': "Error: Telegram messaging system is not ready."}

    # --- Execute Action ---
    try:
        logger.info(f"Attempting to send Telegram message to user ID {recipient_user_id}: '{message_content[:50]}...'")
        
        # Since this execute function is async, we can directly await
        await telegram_handler.send_text_message_to_user(recipient_user_id, message_content)
        
        success_message = f"Telegram message successfully queued for sending to user ID {recipient_user_id}."
        logger.info(success_message)
        return {
            'status': 'success',
            'message': success_message,
            'data': {
                'recipient_user_id': recipient_user_id,
                'message_sent_snippet': message_content[:70] + "..." if len(message_content) > 70 else message_content
            }
        }
    except Exception as e:
        error_message = f"Failed to send Telegram message to user ID {recipient_user_id}: {e}"
        logger.error(error_message, exc_info=True)
        return {'status': 'error', 'message': error_message}