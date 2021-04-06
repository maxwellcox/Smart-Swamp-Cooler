"""
Author: Jayden Smith

Last Modified: April 5, 2021

ECE 4020 - Senior Project II

Main script for SmartSwampCooler project
filename: smartSwampCooler.py

This code runs on the Raspberry Pi 4 Model B acting as the master
controller. 

It reads data from the Zigbee coordinator receiving data it
collects from the sensor router nodes. This serial communication is done
through the serial UART port of the Pi. Functionality is provided for 
serial settings, opening/closing the port, resetting the buffer, and 
reading data any time a message is received. The data is decoded and 
converted into a SensorData class object, then converted to be added to 
the main database storing all sensor readings.

The raw data received from the Zigbee coordinator is decoded and interpreted.
This data is sorted into key-value pairs that are used to create SensorData 
objects. These objects are used as entries into a PHPMyAdmin database using 
MySQL queries. Functionality is provided to read out the data from the database
for the whole database, or for any sensor node and for any number of days in the
past.

This script also reads current cooler settings and desired temperature set by
the user on the website, combines those with the forecasted data from the database
and most recent sensor readings, then feeds all of these inputs into a smart,
predictive algorithm to determine the best cooler setting to achieve desired
temperature. It also incorporates house settings, including efficiency of the cooler,
square footage of the house, volume of air produced by each cooler setting, latitude
and longitude of the device, etc.
"""

import mysql.connector
import sys
import json
import re
import time
import serial
import libs
import RPi.GPIO as GPIO
from gpiozero import LED
from datetime import datetime
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)

# unique IDs for Zigbee sensor nodes
HOME_SENSOR_ID = "0013a200Ac21216"
ROOF_SENSOR_ID = "0013a200Ac1f102"

# Pi GPIO assignments for cooler signals
speed = LED(21)
fan   = LED(20)
pump  = LED(16)

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
# Return the response (which may often be an empty string if nothing received)
def wait_for_serial_response(timeout):
    response = read_serial()
    timer = 0
    while response == b"" and timer < timeout:
        # check user's settings and desired temperature from database
        # and set up algorithm to implement setting
        coolerSetting, desired_temperature = get_cooler_setting()
        set_cooler(coolerSetting, desired_temperature)
        
        # count to 10, sampling serial port every second
        print("  . ({})".format(timeout-timer))
        timer += 1
        time.sleep(1)
        response = read_serial()
    return response

# reset serial port by closing port and flushing buffer
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
#  Define a class for holding sensor data information from db
class SensorData:
    def __init__(self, id="-1", sensor_id="-1", temperature=32.5, humidity=25.7):
        self.id = id
        self.sensor_id = sensor_id
        self.temperature = temperature
        self.humidity = humidity
      
    # these two functions allow for easy printing of sensor data object
    # to check data of a given object...common Python practice
    def __repr__(self):
        return "%s(%r)" % (self.__class__, self.__dict__)
    
    def __print__(self):
        return "%r" % (self.__dict__)
        
# Define a class for holding house setting information 
# from db to pass into smart algorithm
class HouseSettings:
    def __init__(self, id="-1", latitude=0.0, longitude=0.0, house_volume=0.0, lo_fan_volume=0.0, hi_fan_volume=0.0, efficiency=0.0):
        self.id = id
        self.latitude = latitude
        self.longitude = longitude
        self.house_volume = house_volume
        self.lo_fan_volume = lo_fan_volume
        self.hi_fan_volume = hi_fan_volume
        self.efficiency = efficiency
    
    # these two functions allow for easy printing of house settings object
    # to check data of a given object...common Python practice
    def __repr__(self):
        return "%s(%r)" % (self.__class__, self.__dict__)
    
    def __print__(self):
        return "%r" % (self.__dict__)
    
# Connect the mySQL database and provide login credentials
smartswampcooler_db = mysql.connector.connect(
  host="localhost",
  database="smartswampcooler",
  user="pi",
  passwd="jeniffer",
)

# Select all data from the sensor_data table
SQL_SELECT_DATA = "SELECT * FROM sensor_data"

# Insert new row of from SensorData object
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
  ORDER BY timestamp
"""

# Select most recent entry for cooler setting
SQL_SELECT_COOLER_SETTING = """
  SELECT *
  FROM cooler_settings
  ORDER BY timestamp
  DESC LIMIT 1
"""

# Insert new row of cooler settings
SQL_INSERT_SETTINGS = """
 INSERT INTO cooler_settings (timestamp, setting, desiredTemperature)
 VALUES (NOW(), %s, %s)
"""

# Select most recent entry for given sensor ID
SQL_SELECT_RECENT_SENSOR_DATA = """
  SELECT *
  FROM sensor_data
  WHERE sensor_id=%s
  ORDER BY timestamp
  DESC LIMIT 1
"""

# Select most recent entry for given house settings
SQL_SELECT_HOUSE_SETTINGS = """
  SELECT *
  FROM house_settings
  HAVING MAX(id)
"""

# Retrieve forecasted weather from db
SQL_SELECT_FORECAST = """
  SELECT temperature
  FROM forecast
  ORDER BY id
  ASC LIMIT 8
"""

# Maps database data to sensor_data object
def map_to_object(data):
  sensor_data = SensorData()
  
  try:
    # assign database fields to SensorData object
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

# Maps database data to HouseSettings object
def map_to_house_object(data):
  house_settings = HouseSettings()
  
  try:
    # assign database fields to object
    house_settings.id                      = (data["id"])
    house_settings.latitude                = (data["latitude"])
    house_settings.longitude               = (data["longitude"])
    house_settings.lo_fan_volume           = (data["lo_fan_volume"])
    house_settings.hi_fan_volume           = (data["hi_fan_volume"])
    house_settings.house_volume            = (data["house_volume"])
    house_settings.efficiency              = (data["efficiency"])
  
  except Exception as ex:
    print("ERROR: Error mapping database data to house_settings object")
    template = "An exception of type {0} occurred. Arguments:\n{1!r}"
    message = template.format(type(ex).__name__, ex.args)
    print(message)
    return house_settings 
  
  return house_settings

# Read out all entries from the database
def read_all_db():
  # create a connection cursor to the database
  mycursor = smartswampcooler_db.cursor()
  
  # call select all query
  mycursor.execute(SQL_SELECT_DATA)
  
  # this will extract row headers
  row_headers = [x[0] for x in mycursor.description] 
  myresult = mycursor.fetchall()

  # for x in myresult:
  #   print(x)

  # convert SQL entry to Python dict object
  json_data = []
  for result in myresult:
    json_data.append(dict(zip(row_headers,result)))

  data = json.dumps(json_data, indent=4, sort_keys=True, default=str)
  data = json.loads(data)
  #data = data[0]
  #print(data) # print json_data
  
  # print out all json data entries and map to SensorData class object
  for i in range(len(data)):
      print(data[i])
      sensor_data = map_to_object(data[i])
  
  return sensor_data

# Read named sensor entries from database
def read_sensor_db(sensor_name="", days=0):
  # create a new connection cursor
  mycursor = smartswampcooler_db.cursor()
  
  # get chosen sensor data based on id from database
  if sensor_name == "roof":
    params = (ROOF_SENSOR_ID, days,) 
  elif sensor_name == "home":
    params = (HOME_SENSOR_ID, days,)
  else:
    print("ERROR: Invalid sensor name: {}".format(sensor_name))
    print("Specify valid sensor name: 'roof' or 'home'\n")
    return -1
  
  # validate accurate database retrieval
  if days < 1:
    print("ERROR: Specify number of days for sensor data >1\n")
    return -1
  
  # retrieve data from given sensor for given number of days  
  mycursor.execute(SQL_SELECT_SENSOR_DATA, params)
  
  # this will extract row headers
  row_headers = [x[0] for x in mycursor.description] 
  myresult = mycursor.fetchall()

  # convert SQL entry to Python dict object
  json_data = []
  for result in myresult:
    json_data.append(dict(zip(row_headers,result)))

  data = json.dumps(json_data, indent=4, sort_keys=True, default=str)
  data = json.loads(data)
  
  print("Displaying {} sensor data over last {} days: ".format(sensor_name, days))
  # print out all json data entries and map to SensorData class object
  for i in range(len(data)):
      print(data[i])
      sensor_data = map_to_object(data[i])
  
  return sensor_data


# Read most recent house settings from database
def read_house_settings_db():
  house_settings = HouseSettings()
  
  # create new connection cursor
  mycursor = smartswampcooler_db.cursor()

  # retrieve house settings from database
  mycursor.execute(SQL_SELECT_HOUSE_SETTINGS)
  
  # this will extract row headers
  row_headers = [x[0] for x in mycursor.description] 
  myresult = mycursor.fetchall()

  # convert SQL entry to Python dict object
  json_data = []
  for result in myresult:
    json_data.append(dict(zip(row_headers,result)))

  data = json.dumps(json_data, indent=4, sort_keys=True, default=str)
  data = json.loads(data)
  
  #print("Displaying house settings: ")
  # print out all json data entries and map to HouseSettings object
  for i in range(len(data)):
      #print(data[i])
      house_settings = map_to_house_object(data[i])
  
  return house_settings

# Insert new sensor data to the database
def write_db(sensor_data):
  print("Inserting new sensor data...")

  # create new database connection cursor
  mycursor = smartswampcooler_db.cursor()

  # provide SensorData parameters to SQL query to write to database
  params = (sensor_data.sensor_id, sensor_data.temperature, sensor_data.humidity,)

  # write new data to database and commit changes
  mycursor.execute(SQL_INSERT_DATA, params)
  smartswampcooler_db.commit()

  print("{} record(s) affected".format(mycursor.rowcount))
  
# Convert raw XBEE data to appropriate database entry
def xbee_to_object(xb_raw_data):
  # convert from byte data to Python-readable data (utf-8)
  xb_raw_data = xb_raw_data.decode('utf-8')
  
  # format the raw XBEE data to be able to read into json.loads
  xb_data = xb_raw_data.replace(": ", ': "').replace(", ", '", ')\
            .replace("}", '"}').replace("'", '"').replace('""', '"')\
            .replace('"b"', '').replace('}",', "},")\
            .replace('\\x', '').replace('eui64": ', 'eui64": "')
  
  try:
    # convert XBEE data to Python dictionary
    xb_dict = json.loads(xb_data)
  except Exception as ex:
    # print("ERROR: Error loading xb data")
    # template = "An exception of type {0} occurred. Arguments:\n{1!r}"
    #message = template.format(type(ex).__name__, ex.args)
    return -1

  # check sensor unique 64-bit identifier
  # check for ROOF, HOME, or UNKNOWN sensor
  if xb_dict["sender_eui64"] == ROOF_SENSOR_ID or xb_dict["sender_eui64"] == HOME_SENSOR_ID:
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

# insert newest cooler setting to the database
def write_setting_db(coolerSet, desiredTemperature):
    print("Inserting new cooler setting data...")
    
    # create a connection cursor
    mycursor = smartswampcooler_db.cursor()

    # provide new cooler setting parameters to write to database
    # this will only write when in AUTO setting and the algorithm
    # changes settings
    params = (coolerSet, desiredTemperature,)
    
    # write new cooler settings to database and commit changes
    mycursor.execute(SQL_INSERT_SETTINGS, params)
    smartswampcooler_db.commit()
  
    print("{} record(s) affected".format(mycursor.rowcount))
  
# Retrieve most recent cooler setting from database  
def get_cooler_setting():
    # creating a connection cursor
    mycursor = smartswampcooler_db.cursor()
    
    # retrieve data from given sensor for given number of days
    mycursor.execute(SQL_SELECT_COOLER_SETTING)
  
    # this will extract row headers
    row_headers = [x[0] for x in mycursor.description] 
    myresult = mycursor.fetchall()

    # convert SQL entry to Python dict object
    json_data = []
    for result in myresult:
        json_data.append(dict(zip(row_headers,result)))
    
    data = json.dumps(json_data, sort_keys=True, default=str)
    data = json.loads(data)

    # commit database changes
    smartswampcooler_db.commit()
    
    #print("SETTING: ", data[0]["setting"])
    return data[0]["setting"], data[0]["desiredTemperature"]

# retrieve most recent temp/hum/timestamp data from database
def get_recent_data(sensor_name=""):
  sensor_data = SensorData()
  
  # creating a connection cursor
  mycursor = smartswampcooler_db.cursor()
  
  # get chosen sensor data based on id from database
  if sensor_name == "roof":
    params = (ROOF_SENSOR_ID,)
  elif sensor_name == "home":
    params = (HOME_SENSOR_ID,)
  else:
    print("ERROR: Invalid sensor name: {}".format(sensor_name))
    print("Specify valid sensor name: 'roof' or 'home'\n")
    return -1
  
  # retrieve most recent data
  mycursor.execute(SQL_SELECT_RECENT_SENSOR_DATA, params)
  
  # this will extract row headers
  row_headers = [x[0] for x in mycursor.description] 
  myresult = mycursor.fetchall()

  # convert SQL entry to Python dict object
  json_data = []
  for result in myresult:
    json_data.append(dict(zip(row_headers,result)))

  data = json.dumps(json_data, indent=4, sort_keys=True, default=str)
  data = json.loads(data)
  
  # print out all json data entries and map to SensorData object
  for i in range(len(data)):
      #print(data[i])
      sensor_data = map_to_object(data[i])
  
  return sensor_data
   
# Retrieve most recent forecasted data from database to feed 
# into smart algorithm
def get_forecast_db():
  # create a connection cursor to the database
  mycursor = smartswampcooler_db.cursor()
  
  # retrieve forecasted data from database
  mycursor.execute(SQL_SELECT_FORECAST)
  myresult = mycursor.fetchall()

  # convert query into list of temperatures to feed into smart alg
  temperatures = []
  for result in myresult:
    temperatures.append(result[0])

  #print(temperatures)
  return temperatures

# take current cooler setting and desired temperature and determine
# correct cooler setting to turn on/off
# will pick manual setting if designated by user, or will call smart
# algorithm if run in AUTO mode
def set_cooler(setting, desired_temperature):
    if setting[0:4] == 'Auto':
        # run sweet, smart, predictive algorithm
        print('Auto')
        # get old setting to see if database setting needs to be updated
        temp = setting[5:]
        #print('Old Setting: ' + temp + '\n')
        
        # call smart algorithm to determine appropriate cooler setting
        # feed in most recent temperature/humidity readings from sensors
        recent_roof_data = get_recent_data(sensor_name="roof")
        recent_home_data = get_recent_data(sensor_name="home")
        
        # get all house settings parameters for algorithm
        house_settings = read_house_settings_db()
        
        # retrieve forecasted temperatures and append to recent sensor reading
        roof_temperatures = [recent_roof_data.temperature,] + get_forecast_db()
        
        # run SMART ALGORITHM!
        setting = libs.get_auto_setting(roof_temperatures, recent_roof_data.humidity,\
                                        recent_home_data.temperature, recent_home_data.humidity,\
                                        house_settings, desired_temp=desired_temperature)
        #print('New Setting: ' + setting)
        
        # only write to the database if setting changes
        if setting != temp:
            write_setting_db('Auto ' + setting, desired_temperature)
    
    if setting == 'Off':
        # turn off all GPIO signals
        print('Off')
        pump.off()
        fan.off()
        speed.off()
    elif setting == 'Pump':
        # turn on Pump GPIO
        print('Pump')
        pump.on()
        fan.off()
        speed.off()
    elif setting == 'Fan Hi':
        # turn on Fan Hi GPIO
        print('Fan Hi')
        pump.off()
        fan.on()
        speed.on()
    elif setting == 'Fan Lo':
        # turn on Fan Lo GPIO
        print('Fan Lo')
        pump.off()
        fan.on()
        speed.off()
    elif setting == 'Fan Hi (w/Pump)':
        # turn on Fan Hi w/Pump GPIO
        print('Fan Hi (w/Pump)')
        pump.on()
        fan.on()
        speed.on()
    elif setting == 'Fan Lo (w/Pump)':
        # turn on Fan Lo w/Pump GPIO
        print('Fan Lo (w/Pump)')
        pump.on()
        fan.on()
        speed.off()
    
# ---------------------------------------------------------------------- #

if __name__== "__main__":
  # main loop that controls all relays, sensor readings, and database
  while(1):
    # get raw Zigbee data from serial port
    raw_xb_data = wait_for_serial_response(10)
    
    # if actual data received, convert to Python and database object
    if raw_xb_data != b"":
        sensor_data = xbee_to_object(raw_xb_data)
        if ( sensor_data != -1 ):
            display_sensor_data(sensor_data)
            # write newest sensor readings to database
            write_db(sensor_data)
            # read_sensor_db(sensor_name="home", days=2) 
        
            # check updated database contents
            # read_all_db()

    # flush serial input buffer and reopen port
    reset_serial_port()
  
    print("Finished\n")
    # wait 5 seconds at the end of each loop for optimal power use
    time.sleep(5)
   
# ---------------------------------------------------------------------- #
# ---------------------------------------------------------------------- #   
# Below are some handy testing checks used throughout the project:   
    # verify writing to the database with SensorData class and db functions above
    # sensor_data = SensorData()
    # sensor_data = read_all_db()
    # new_data = SensorData(id="7", sensor_id="1", temperature=10.1, humidity=150.2)
    # write_db(new_data)  
    # read_all_db()

    # check contents of the database
    # sensor_data = SensorData()
    # sensor_data = read_all_db()
    # extract all db entries for given sensor
    # read_sensor_db(sensor_name="roof")
    # read_sensor_db(sensor_name="home")