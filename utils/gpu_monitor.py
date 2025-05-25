# utils/gpu_monitor.py
import threading
import time
import sys

# Assuming logger.py is in the same 'utils' directory
from logger import get_logger

logger = get_logger(__name__)

PYNVML_AVAILABLE = False
nvml = None # nvml module itself, not an instance
try:
    from pynvml import nvmlInit, nvmlDeviceGetHandleByIndex, nvmlDeviceGetMemoryInfo, \
                       nvmlDeviceGetUtilizationRates, nvmlShutdown, NVMLError # Removed nvmlDeviceGetName
    nvmlInit()
    PYNVML_AVAILABLE = True
    logger.info("NVML initialized successfully.")
except ImportError:
    logger.warning("pynvml library not found. GPU monitoring for NVIDIA GPUs will be disabled.")
    PYNVML_AVAILABLE = False # Explicitly set, though it would be by default
except NVMLError as e:
    logger.error(f"NVML Error during initialization: {e}. GPU monitoring may be unavailable.", exc_info=False) # No need for full trace for init error here
    PYNVML_AVAILABLE = False
except Exception as e:
    logger.error(f"Unexpected error during pynvml import/init: {e}", exc_info=True)
    PYNVML_AVAILABLE = False


class GPUMonitor:
    def __init__(self, update_interval_sec=2, gpu_index=0, gui_callbacks=None):
        if not PYNVML_AVAILABLE:
            self.active = False
            self.gpu_handle = None
            self.monitor_thread = None
            logger.warning("GPUMonitor: Cannot start, NVML not available or failed to initialize.")
            if gui_callbacks and 'gpu_status_update_display' in gui_callbacks:
                gui_callbacks['gpu_status_update_display']("N/A", "N/A", "na_nvml")
            return

        self.active = True
        self.update_interval_sec = update_interval_sec
        self.gpu_index = gpu_index
        self.gui_callbacks = gui_callbacks
        self.stop_event = threading.Event()
        self.gpu_handle = None

        try:
            self.gpu_handle = nvmlDeviceGetHandleByIndex(self.gpu_index)
            # For logging purposes, it's fine to get the name once if desired, but not strictly needed for GUI.
            # from pynvml import nvmlDeviceGetName # Local import if only used here
            # gpu_name_temp = nvmlDeviceGetName(self.gpu_handle).decode('utf-8')
            # logger.info(f"Monitoring GPU {self.gpu_index}: {gpu_name_temp}")
            logger.info(f"Successfully got handle for GPU {self.gpu_index}.")


            if self.gui_callbacks and 'gpu_status_update_display' in self.gui_callbacks:
                gui_callbacks['gpu_status_update_display']("...", "...", "checking")
        except NVMLError as e:
            error_msg = f"Could not get handle for GPU {self.gpu_index}. Error: {e}"
            logger.error(error_msg, exc_info=False) # No need for full trace here
            self.active = False
            if self.gui_callbacks and 'gpu_status_update_display' in self.gui_callbacks:
                gui_callbacks['gpu_status_update_display']("ERR", "ERR", "error")
            if self.gui_callbacks and 'messagebox_warn' in self.gui_callbacks:
                self.gui_callbacks['messagebox_warn']("GPU Monitor Error", f"Could not initialize monitoring for GPU {self.gpu_index}: {e}")
            return

        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True, name="GPUMonitorThread")

    def start(self):
        if self.active and self.monitor_thread and not self.monitor_thread.is_alive():
            self.stop_event.clear()
            self.monitor_thread.start()
            logger.info("GPUMonitor thread started.")
        elif not self.active:
            logger.warning("GPUMonitor not starting, monitor is inactive (NVML issue or init failure).")
        elif self.monitor_thread and self.monitor_thread.is_alive():
            logger.info("GPUMonitor thread already running.")


    def _monitor_loop(self):
        logger.debug("GPUMonitor loop entered.")
        while not self.stop_event.is_set() and self.active and self.gpu_handle:
            try:
                mem_info = nvmlDeviceGetMemoryInfo(self.gpu_handle)
                util_rates = nvmlDeviceGetUtilizationRates(self.gpu_handle)

                mem_used_mb = mem_info.used / (1024**2)
                mem_total_mb = mem_info.total / (1024**2)
                gpu_util = util_rates.gpu

                mem_text = f"{mem_used_mb:.0f}/{mem_total_mb:.0f}MB"
                util_text = f"{gpu_util}%"
                logger.debug(f"GPU {self.gpu_index} Stats: Mem: {mem_text}, Util: {util_text}")


                if self.gui_callbacks and 'gpu_status_update_display' in self.gui_callbacks:
                    self.gui_callbacks['gpu_status_update_display'](
                        mem_text,
                        util_text,
                        "ok_gpu"
                    )
            except NVMLError as e:
                logger.error(f"NVML Error in monitor loop for GPU {self.gpu_index}: {e}. Stopping loop.", exc_info=False) # No need for trace every time
                if self.gui_callbacks and 'gpu_status_update_display' in self.gui_callbacks:
                    self.gui_callbacks['gpu_status_update_display'](
                         "ERR", "ERR", "error_nvml_loop"
                    )
                self.active = False # Stop trying if NVML errors persist in loop
                break
            except Exception as e:
                logger.error(f"Unexpected error in GPUMonitor loop: {e}", exc_info=True)
                self.active = False # Stop on unexpected errors
                break
            time.sleep(self.update_interval_sec)
        logger.info("GPUMonitor loop finished.")

    def stop(self):
        logger.info("GPUMonitor stop called.")
        self.stop_event.set()
        if self.monitor_thread and self.monitor_thread.is_alive():
            logger.info("Joining GPUMonitor thread...")
            self.monitor_thread.join(timeout=self.update_interval_sec + 1)
            if self.monitor_thread.is_alive():
                logger.warning("GPUMonitor thread did not join cleanly.")
        self.active = False
        logger.info("GPUMonitor stopped.")


    def cleanup_nvml(self):
        if PYNVML_AVAILABLE: # Only attempt if it was available in the first place
            try:
                nvmlShutdown()
                logger.info("NVML shutdown successfully.")
            except NVMLError as e:
                logger.error(f"NVML Error during shutdown: {e}", exc_info=False)
            except Exception as e: # Catch any other potential errors during shutdown
                logger.error(f"General error during NVML shutdown call: {e}", exc_info=True)

_gpu_monitor_instance = None

def get_gpu_monitor_instance(gui_callbacks=None, update_interval=2, gpu_index=0):
    global _gpu_monitor_instance
    if _gpu_monitor_instance is None and PYNVML_AVAILABLE:
        logger.debug("Creating new GPUMonitor instance.")
        _gpu_monitor_instance = GPUMonitor(
            update_interval_sec=update_interval,
            gpu_index=gpu_index,
            gui_callbacks=gui_callbacks
        )
    elif _gpu_monitor_instance is None and not PYNVML_AVAILABLE:
        logger.warning("GPUMonitor instance not created, PYNVML not available.")
        if gui_callbacks and 'gpu_status_update_display' in gui_callbacks:
            gui_callbacks['gpu_status_update_display']("N/A", "N/A", "na_nvml")
    elif _gpu_monitor_instance:
        logger.debug("Returning existing GPUMonitor instance.")
    return _gpu_monitor_instance

def shutdown_gpu_monitor():
    global _gpu_monitor_instance
    logger.info("Shutting down GPU monitor instance (if active).")
    if _gpu_monitor_instance:
        _gpu_monitor_instance.stop()
        _gpu_monitor_instance.cleanup_nvml() # Instance specific cleanup
        _gpu_monitor_instance = None
        logger.info("GPUMonitor instance stopped and NVML cleaned up via instance.")
    elif PYNVML_AVAILABLE : # If no instance, but NVML was loaded, try a global shutdown
        logger.info("No active GPUMonitor instance, attempting direct NVML shutdown.")
        try:
            nvmlShutdown() # Ensure NVML is shut down even if monitor wasn't fully used
            logger.info("Direct NVML shutdown successful.")
        except NVMLError as e:
            logger.error(f"NVML Error during direct shutdown (no instance): {e}", exc_info=False)
        except Exception as e: # Catch any other potential errors during shutdown
            logger.error(f"General error during direct NVML shutdown (no instance): {e}", exc_info=True)
    else:
        logger.info("NVML was not available, no GPU monitor shutdown actions needed.")