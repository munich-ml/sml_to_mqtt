# inspired by https://github.com/huirad/pysml

import logging, os, serial, time
from copy import deepcopy
from ruamel.yaml import YAML


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
        
    def __init__(self, offsets: dict, port: str, baudrate: int = 9600, timeout: int = 3):
        self.ser = serial.Serial(port, baudrate=baudrate, timeout=timeout)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()
        self._offsets = offsets    # {'energy_imported': 171, 'energy_exported': 202}
        self._last_values = {entity: 0 for entity in offsets.keys()}  # initialize to 0
        self._last_time_updated = time.time()                            
        
        
    def __del__(self):  
        logging.info("Teardown: Closing serial connection")
        self.ser.close()    
        
        
    def _read_message(self):
        """Read the next SML transport message from the serial device

        Args:
            ser Serial: serial device handle

        Returns:
            bytes: SML Message
        """
        # search for the 1st escape sequence - it may be for start or end
        t_esc = self.ser.read_until(SmlClient.ESCAPE_SEQUENCE)
        if not t_esc.endswith(SmlClient.ESCAPE_SEQUENCE):
            raise ValueError("ESCAPE_SEQUENCE not found!")
        
        # search for the packet starting with START_MESSAGE and ending with ESCAPE_SEQUENCE
        MAX_READ = 5 #limit the number of read attemps to avoid endless loop
        for _ in range(MAX_READ):
            t_msg = self.ser.read_until(SmlClient.ESCAPE_SEQUENCE)
            if t_msg.startswith(SmlClient.START_MESSAGE):
                break
        else:
            raise ValueError("START_MESSAGE not found!")
            
        
        # verify that the terminating ESCAPE_SEQUENCE is followed by END_MESSAGE
        t_end = self.ser.read(4)
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
            before = deepcopy(self._last_values)    
            
            change = change or val != self._last_values[entity]
            self._last_values[entity] = val
            after = deepcopy(self._last_values)    
            if change:
                logging.debug(f"before={before}, after={after}, val={val}")
            
        hourly_update = time.time() - self._last_time_updated > 3600
        
        if change or hourly_update:
            self._last_time_updated = time.time()
            logging.debug(f"before={before}, after={after}, val={val}, change={change}")
            return deepcopy(self._last_values)
    
    
class YamlInterface:
    """Helper class for load and dump yaml files. Preserves comments and quotes.
    """
    def __init__(self, filename):
        self.filename = filename
        
        # create a ruamel.yaml object
        self._yaml = YAML()
        self._yaml.preserve_quotes = True
        
    def load(self):
        with open(self.filename, 'r') as f:
            data = self._yaml.load(f)
        return data
    
    def dump(self, data):
        with open(self.filename, 'w') as f:
            self._yaml.dump(data, f)
                

if __name__ == "__main__":
    settings = YamlInterface(os.path.join(wd, SETTINGS)).load()
    entities = YamlInterface(os.path.join(wd, ENTITIES)).load()
    
    kwargs = dict(settings["USB"])
    kwargs["offsets"] = {name: params["offset"] for name, params in entities.items() if "offset" in params}
    
    logging.info(f"Starting SmlClient with {kwargs}")
    sml_client = SmlClient(**kwargs)
    
    while True:
        vals = sml_client.read()
        if vals is not None:
            logging.info(f"value update {vals}")     

        time.sleep(10)