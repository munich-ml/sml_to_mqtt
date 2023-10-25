# inspired by https://github.com/huirad/pysml

import logging, os, serial, time
from copy import deepcopy
from mqtt_device import MqttDevice, YamlInterface

# Constants
SETTINGS = 'settings.yaml'
ENTITIES = 'entities.yaml'
SECRETS = 'secrets.yaml'
LOGFILE = "logging.txt"
LOGLEVEL = logging.DEBUG


# setup logging
wd = os.path.dirname(__file__)
logging.basicConfig(format='%(asctime)s | %(levelname)-7s | %(filename)s line=%(lineno)s | %(message)s',
                    handlers=[logging.FileHandler(os.path.join(wd, LOGFILE)), 
                              logging.StreamHandler(),], level=LOGLEVEL)

class SmlClient():
    START_MESSAGE = b'\x01\x01\x01\x01'
    ESCAPE_SEQUENCE = b'\x1b\x1b\x1b\x1b'
    END_MESSAGE = b'\x1a'
        
    def __init__(self, offsets: dict, port: str, baudrate: int = 9600, timeout: int = 3, max_update_interval: int = 3600):
        """SML Client constructor

        Args:
            offsets (dict): Offsets of entities within the SML message
            port (str): Serial port, like "COM7" or '/dev/ttyUSB0'
            baudrate (int): Serial baud rate. Defaults to 9600.
            timeout (int): Serial timeout in seconds. Defaults to 3.
            max_update_interval (int): SmlClient.read() returns data only on changes or if this interval in seconds is 
                exceeded. Defaults to 3600.
        """
        self._ser = serial.Serial(port, baudrate=baudrate, timeout=timeout)
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()
        self._max_update_interval = max_update_interval
        self._offsets = offsets    # {'energy_imported': 171, 'energy_exported': 202}
        self._last_values = {entity: 0 for entity in offsets.keys()}  # initialize to 0
        self._last_time_updated = time.time()                            
        
        
    def __del__(self):
        """Teardown: Closing serial connection
        """  
        self._ser.close()    
        
        
    def _read_message(self):
        """Read the next SML transport message from the serial device

        Args:
            ser Serial: serial device handle

        Returns:
            bytes: SML Message
        """
        # search for the 1st escape sequence - it may be for start or end
        t_esc = self._ser.read_until(SmlClient.ESCAPE_SEQUENCE)
        if not t_esc.endswith(SmlClient.ESCAPE_SEQUENCE):
            raise ValueError("ESCAPE_SEQUENCE not found!")
        
        # search for the packet starting with START_MESSAGE and ending with ESCAPE_SEQUENCE
        MAX_READ = 5 #limit the number of read attemps to avoid endless loop
        for _ in range(MAX_READ):
            t_msg = self._ser.read_until(SmlClient.ESCAPE_SEQUENCE)
            if t_msg.startswith(SmlClient.START_MESSAGE):
                break
        else:
            raise ValueError("START_MESSAGE not found!")
            
        
        # verify that the terminating ESCAPE_SEQUENCE is followed by END_MESSAGE
        t_end = self._ser.read(4)
        if not t_end.startswith(SmlClient.END_MESSAGE):
            raise ValueError("END_MESSAGE not found!")        

        return t_msg


    def _get_value(self, msg: bytes, offset: int):
        """Get the integer value from the msg at the given offset.
        Size and signed-ness are determined automatically

        Returns:
            bytes: On succes, The extracted integer value.
        """
        if (len(msg)-offset) < 2:
            return

        if (msg[offset] & 0xF0) in [0x50, 0x60]:  # signed integer, unsigned integer
            size = (msg[offset] & 0x0F)           # size including the 1-byte tag
            if (len(msg)-offset) >= size:
                val = msg[offset+1:offset+size]
                signed = (msg[offset] & 0xF0) == 0x50
                return int.from_bytes(val, byteorder='big', signed=signed)
            
            
    def read(self): 
        """Reads from the SML interface

        Returns:
            NoneType | dict: Dict with entity keys and values like {'energy_imported': 140, 'energy_exported': 2200}
        """
        try:
            msg = self._read_message()
        except ValueError as e:
            logging.warning(f"_read_message() failed with exception {e}")
            return
        
        change = False
        for entity, offset in self._offsets.items():
            val = self._get_value(msg, offset) 
            if val is None:
                logging.warning(f"_get_value() returned None")    
                return
            change = change or val != self._last_values[entity]
            self._last_values[entity] = val
        
        if change or time.time() - self._last_time_updated > self._max_update_interval:
            self._last_time_updated = time.time()
            return deepcopy(self._last_values)
           

if __name__ == "__main__":
    settings = YamlInterface(os.path.join(wd, SETTINGS)).load()
    entities = YamlInterface(os.path.join(wd, ENTITIES)).load()
    
    # Start MqttDevice
    while True:  # this endless loop helps starting the script at raspi boot, when network is not available
        try:
            mqtt = MqttDevice(entities=entities, secrets_path=os.path.join(wd, SECRETS), 
                            on_message_callback=lambda: logging.error("no MQTT write implemented!"),
                            **settings['mqtt'])    
        except Exception as e:
            RETRY_DELAY = 5
            logging.error(f"{e}, trying to reconnect in {RETRY_DELAY} seconds,...")
            time.sleep(RETRY_DELAY)
        else:
            break
    
    # Start SML Client
    kwargs = dict(settings["sml"])
    kwargs["offsets"] = {name: params["offset"] for name, params in entities.items() if "offset" in params}
    logging.info(f"Starting SmlClient with {kwargs}")
    sml_client = SmlClient(**kwargs)
    
    # Endless-loop
    try: 
        while True:
            states = sml_client.read()
            if states is not None:
                mqtt.set_states(states)
                mqtt.publish_updates() 
                logging.debug(f"MQTT publish: {states}")
            time.sleep(settings["app"]["polling_interval"])
    except Exception as e:
        logging.error(f"Exiting app because of Exception {e}")
    finally:   # teardown 
        sml_client.__del__()
        mqtt.exit()
    
    