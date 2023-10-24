# inspired by https://github.com/huirad/pysml

import json, os, serial, time
import datetime as dt
import pandas as pd


def read_message(ser):
    """Read the next SML transport message from the serial device

    Args:
        ser Serial: serial device handle

    Returns:
        bytes: SML Message
    """
    START_MESSAGE = b'\x01\x01\x01\x01'
    ESCAPE_SEQUENCE = b'\x1b\x1b\x1b\x1b'
    END_MESSAGE = b'\x1a'
    
    # search for the 1st escape sequence - it may be for start or end
    t_esc = ser.read_until(ESCAPE_SEQUENCE)
    if not t_esc.endswith(ESCAPE_SEQUENCE):
        raise ValueError("ESCAPE_SEQUENCE not found!")
    
    # search for the packet starting with START_MESSAGE and ending with ESCAPE_SEQUENCE
    MAX_READ = 5 #limit the number of read attemps to avoid endless loop
    for _ in range(MAX_READ):
        t_msg = ser.read_until(ESCAPE_SEQUENCE)
        if t_msg.startswith(START_MESSAGE):
            break
    else:
        raise ValueError("START_MESSAGE not found!")
        
    
    # verify that the terminating ESCAPE_SEQUENCE is followed by END_MESSAGE
    t_end = ser.read(4)
    if not t_end.startswith(END_MESSAGE):
        raise ValueError("END_MESSAGE not found!")        

    return t_msg


def get_value(buffer: bytes, offset: int):
    """Get the integer value from the buffer at the given offset.
    Size and signed-ness are determined automatically

    Returns:
        bytes: On succes, The extracted integer value.
        NoneType: On failure
    """
    
    if (len(buffer)-offset) < 2:
        return

    if (buffer[offset] & 0xF0) in [0x50, 0x60]:  # signed integer, unsigned integer
        size = (buffer[offset] & 0x0F)           # size including the 1-byte tag
        if (len(buffer)-offset) >= size:
            val = buffer[offset+1:offset+size]
            signed = (buffer[offset] & 0xF0) == 0x50
            return int.from_bytes(val, byteorder='big', signed=signed)
    

if __name__ == "__main__":
    OFFSETS = {
        171: "Total energy imported",
        202: "Total energy exported",   
    }

    PORT = "/dev/ttyAMA0"
    PORT = "COM7"
    with serial.Serial(PORT, baudrate=9600, timeout=3) as ser:
        while True:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            try:
                t_msg = read_message(ser)
            except ValueError as e:
                print(e)
            else:   
                print(f"### update {dt.datetime.now()} ###")     
                for offset, name in OFFSETS.items():
                    value = get_value(t_msg, offset)
                    print(f"- {name}: {value}")
                time.sleep(10)