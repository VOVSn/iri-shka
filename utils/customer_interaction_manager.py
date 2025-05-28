# utils/customer_interaction_manager.py
import time
import threading
from logger import get_logger # Assuming logger.py is in project root
import config # For TELEGRAM_NON_ADMIN_MESSAGE_AGGREGATION_DELAY_SECONDS

logger = get_logger("Iri-shka_App.CustomerInteractionManager")

class CustomerInteractionManager:
    def __init__(self):
        # Stores {telegram_user_id: expiry_unix_timestamp}
        self._active_customer_aggregation_timers = {}
        self._lock = threading.Lock() # To protect access to the timers dictionary
        logger.info("CustomerInteractionManager initialized.")

    def record_customer_activity(self, telegram_user_id: int):
        """
        Records activity for a customer, effectively starting or resetting their message aggregation timer.
        This should be called each time a non-admin customer (in an appropriate stage) sends a message.
        """
        with self._lock:
            expiry_timestamp = time.time() + config.TELEGRAM_NON_ADMIN_MESSAGE_AGGREGATION_DELAY_SECONDS
            self._active_customer_aggregation_timers[telegram_user_id] = expiry_timestamp
            logger.debug(f"Aggregation timer updated/set for customer {telegram_user_id}. Expires around {time.ctime(expiry_timestamp)}.")

    def clear_customer_timer(self, telegram_user_id: int):
        """
        Explicitly clears a customer's aggregation timer.
        Useful if processing starts due to an explicit trigger rather than timeout.
        """
        with self._lock:
            if telegram_user_id in self._active_customer_aggregation_timers:
                del self._active_customer_aggregation_timers[telegram_user_id]
                logger.info(f"Aggregation timer explicitly cleared for customer {telegram_user_id}.")

    def check_and_get_expired_interactions(self) -> list[int]:
        """
        Checks for customers whose aggregation delay has passed.
        Returns a list of telegram_user_ids for whom processing should be triggered.
        Removes expired users from the active timers list.
        """
        expired_user_ids = []
        current_time = time.time()
        
        # Iterate over a copy of keys if modifying the dict, or collect then delete
        users_to_check = []
        with self._lock:
            users_to_check = list(self._active_customer_aggregation_timers.keys())

        if not users_to_check:
            return []

        users_to_remove_from_active = []
        for user_id in users_to_check:
            with self._lock: # Re-acquire lock to safely access expiry_time
                expiry_time = self._active_customer_aggregation_timers.get(user_id)
            
            if expiry_time is None: # Should not happen if key was in users_to_check
                continue

            if current_time >= expiry_time:
                expired_user_ids.append(user_id)
                users_to_remove_from_active.append(user_id)
                logger.info(f"Aggregation timer expired for customer {user_id}. Marked for processing.")
        
        if users_to_remove_from_active:
            with self._lock:
                for user_id in users_to_remove_from_active:
                    if user_id in self._active_customer_aggregation_timers: # Check again before deleting
                        del self._active_customer_aggregation_timers[user_id]
        
        if expired_user_ids:
            logger.info(f"Found {len(expired_user_ids)} customer(s) with expired aggregation timers: {expired_user_ids}")
        return expired_user_ids

    def get_active_timer_count(self) -> int:
        """Returns the number of customers currently in an aggregation window."""
        with self._lock:
            return len(self._active_customer_aggregation_timers)

# --- Example Usage (for testing or integration in main.py) ---
if __name__ == '__main__':
    # Configure a simple logger for standalone testing
    import logging
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    manager = CustomerInteractionManager()
    
    # Simulate user 1 sending messages
    manager.record_customer_activity(1001)
    logger.info(f"Active timers: {manager.get_active_timer_count()}")
    time.sleep(2)
    manager.record_customer_activity(1001) # Another message, resets timer
    
    # Simulate user 2 sending a message
    manager.record_customer_activity(1002)
    logger.info(f"Active timers: {manager.get_active_timer_count()}")

    # Test clearing a timer
    manager.clear_customer_timer(1002)
    logger.info(f"Active timers after clearing 1002: {manager.get_active_timer_count()}")
    manager.record_customer_activity(1002) # User 2 sends another message

    # Simulate waiting for timers to expire
    # Set a short delay for testing
    original_delay = config.TELEGRAM_NON_ADMIN_MESSAGE_AGGREGATION_DELAY_SECONDS
    config.TELEGRAM_NON_ADMIN_MESSAGE_AGGREGATION_DELAY_SECONDS = 5 # Temporarily shorten for test
    
    manager.record_customer_activity(1003) # New user with short delay

    logger.info("Waiting for timers to expire (simulated short delay)...")
    time.sleep(config.TELEGRAM_NON_ADMIN_MESSAGE_AGGREGATION_DELAY_SECONDS + 2) # Wait a bit longer than the delay
    
    expired_users = manager.check_and_get_expired_interactions()
    if expired_users:
        logger.info(f"Processing expired users: {expired_users}")
        for user_id in expired_users:
            logger.info(f"-> Would trigger LLM summary for user {user_id}")
    else:
        logger.info("No users expired in this check.")
    
    logger.info(f"Active timers after check: {manager.get_active_timer_count()}")

    # Reset config for other potential tests or imports
    config.TELEGRAM_NON_ADMIN_MESSAGE_AGGREGATION_DELAY_SECONDS = original_delay
    logger.info("Test complete.")