import mysql.connector
import sys
import json
import re
import time
import serial
import RPi.GPIO as GPIO
from datetime import datetime
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)

# unique IDs for Zigbee sensor nodes
ROOF_SENSOR_ID = "0013a200Ac21216"
HOME_SENSOR_ID = "0013a200Ac1f102"

# ---------------------- Pi 4 Serial Functionality ---------------------- # 
# 
# Setup for Serial port to match Zigbee protocol
ser = serial.Serial(
    port = '/dev/ttyS0',
    baudrate = 57600,
    parity = serial.PARITY_NONE,
    stopbits = serial.STOPBITS_ONE,
    bytesize = serial.EIGHTBITS,
    timeout = 1
)

# Read and return data in the serial receive buffer
def read_serial():
    return ser.readline()

# Write data out the serial pin
def write_serial(the_char):
    ser.write(the_char)

# Clear the serial read buffer
def flush_read_buffer():
    ser.reset_input_buffer()

# Wait for a response to be received on serial line or a timeout to occur.
# Return the response (which may be an empty string if nothing received)
def wait_for_serial_response(timeout):
    response = read_serial()
    timer = 0
    while response == "" and timer < timeout:
        print("  . ({})").format(timeout-timer)
        timer += 1
        time.sleep(1)
        response = read_serial()
    return response

def reset_serial_port():
    # close/open serial port
    ser.close()
    time.sleep(1)
    ser.open()

    # flush 
    flush_read_buffer()

# ---------------------------------------------------------------------- #  

# ----------------------- Database Functionality ----------------------- #  
# 
#  Define a class for holding database entry information
class SensorData:
    def __init__(self, id="-1", sensor_id="-1", temperature=32.5, humidity=25.7):
        self.id = id
        self.sensor_id = sensor_id
        self.temperature = temperature
        self.humidity = humidity

# Connect the mySQL database and provide login credentials
smartswampcooler_db = mysql.connector.connect(
  host="localhost",
  database="smartswampcooler",
  user="pi",
  passwd="jeniffer",
)

# Select all data from the sensor_data table
SQL_SELECT_DATA = "SELECT * FROM sensor_data"

# Insert new row of Sensor Data
SQL_INSERT_DATA = """
 INSERT INTO sensor_data (sensor_id, timestamp, temperature, humidity)
 VALUES (%s, NOW(), %s, %s)
"""

# Select all data from given sensor and interval (# of days previous)
SQL_SELECT_SENSOR_DATA = """
  SELECT *
  FROM sensor_data
  WHERE sensor_id=%s
  AND timestamp >= NOW() - INTERVAL %s DAY
"""

# Maps database data to sensor_data object
def map_to_object(data):
  try:
    # assign database fields to object
    sensor_data.id                      = (data["id"])
    sensor_data.sensor_id               = (data["sensor_id"])
    sensor_data.timestamp               = str(data["timestamp"])
    sensor_data.temperature             = (data["temperature"])
    sensor_data.humidity                = (data["humidity"])
  
  except Exception as ex:
    print("ERROR: Error mapping database data to sensor_data object")
    template = "An exception of type {0} occurred. Arguments:\n{1!r}"
    message = template.format(type(ex).__name__, ex.args)
    print(message)
    return sensor_data 
  
  return sensor_data

# Read out all entries from the databse
def read_all_db():
  mycursor = smartswampcooler_db.cursor()
  mycursor.execute(SQL_SELECT_DATA)
  
  # this will extract row headers
  row_headers = [x[0] for x in mycursor.description] 
  myresult = mycursor.fetchall()

  # for x in myresult:
  #   print(x)

  json_data = []
  for result in myresult:
    json_data.append(dict(zip(row_headers,result)))

  data = json.dumps(json_data, indent=4, sort_keys=True, default=str)
  data = json.loads(data)
  #data = data[0]
  #print(data) # print json_data
  
  # print out all json data entries
  for i in range(len(data)):
      print(data[i])
      sensor_data = map_to_object(data[i])
  
  return sensor_data

# Read named sensor entries from database
def read_sensor_db(sensor_name="", days=0):
  mycursor = smartswampcooler_db.cursor()

  if sensor_name == "roof":
    params = (ROOF_SENSOR_ID, days,)
  elif sensor_name == "home":
    params = (HOME_SENSOR_ID, days,)
  else:
    print("ERROR: Invalid sensor name: {}".format(sensor_name))
    print("Specify valid sensor name: 'roof' or 'home'\n")
    return -1
  
  if days < 1:
    print("ERROR: Specify number of days for sensor data >1\n")
    
  mycursor.execute(SQL_SELECT_SENSOR_DATA, params)
  
  # this will extract row headers
  row_headers = [x[0] for x in mycursor.description] 
  myresult = mycursor.fetchall()

  json_data = []
  for result in myresult:
    json_data.append(dict(zip(row_headers,result)))

  data = json.dumps(json_data, indent=4, sort_keys=True, default=str)
  data = json.loads(data)
  
  print("Displaying {} sensor data over last {} days: ".format(sensor_name, days))
  # print out all json data entries
  for i in range(len(data)):
      print(data[i])
      sensor_data = map_to_object(data[i])
  
  return sensor_data

# Insert new sensor data to the database
def write_db(sensor_data):
  print("Inserting new sensor data...")

  mycursor = smartswampcooler_db.cursor()

  params = (sensor_data.sensor_id, sensor_data.temperature, sensor_data.humidity,)

  mycursor.execute(SQL_INSERT_DATA, params)
  smartswampcooler_db.commit()

  print("{} record(s) affected".format(mycursor.rowcount))
  
# Convert raw XBEE data to appropriate database entry
def xbee_to_object(xb_raw_data):
  # format the raw XBEE data to be able to read into json.loads
  xb_data = xb_raw_data.replace(": ", ': "').replace(", ", '", ')\
            .replace("}", '"}').replace("'", '"').replace('""', '"')\
            .replace('"b"', '').replace('}",', "},")\
            .replace('\\x', '').replace('eui64": ', 'eui64": "')

  # convert XBEE data to Python dictionary
  xb_dict = json.loads(xb_data)

  # check sensor unique 64-bit identifier
  # check for ROOF, HOME, or UNKNOWN sensor
  if xb_dict["sender_eui64"] == ROOF_SENSOR_ID:
      sensor_id = xb_dict["sender_eui64"]
  elif xb_dict["sender_eui64"] == HOME_SENSOR_ID:
      sensor_id = xb_dict["sender_eui64"]
  else: 
      print("ERROR: Unrecognized Zigbee sensor id.\n")
      print("Unknown id: {}\n".format(xb_dict["sender_eui64"]))
      return -1

  # return formatted sensor data for db entry
  sensor_data = SensorData(sensor_id=sensor_id, 
                      temperature=xb_dict["payload"]["Temperature"],
                      humidity=xb_dict["payload"]["Humidity"])
  return sensor_data

# print out the contents of the SensorData class object
def display_sensor_data(sensor_data):
  print("Sensor ID: '{}', Temperature: '{}', Humidity: '{}'".format(\
      sensor_data.sensor_id, sensor_data.temperature, sensor_data.humidity))

# ---------------------------------------------------------------------- #
if __name__== "__main__":
  # verify writing to the database with SensorData class and db functions above
  # sensor_data = SensorData()
  # sensor_data = read_all_db()
  # new_data = SensorData(id="7", sensor_id="1", temperature=10.1, humidity=150.2)
  # write_db(new_data)  
  # read_all_db()

  # check contents of the database
  # sensor_data = SensorData()
  # sensor_data = read_all_db()
  while(1):    
    raw_xb_data = wait_for_serial_response(30)

    if raw_xb_data != "":
        sensor_data = SensorData()
        sensor_data = xbee_to_object(raw_xb_data)
        display_sensor_data(sensor_data)
        write_db(sensor_data)
        read_sensor_db(sensor_name="roof", days=2)
        
        # check updated database contents
        # read_all_db()

    # flush serial input buffer and reopen port
    reset_serial_port()
  
    print("Finished\n")
    time.sleep(5)
    
    # extract all db entries for given sensor
    # read_sensor_db(sensor_name="roof")
    # read_sensor_db(sensor_name="home")