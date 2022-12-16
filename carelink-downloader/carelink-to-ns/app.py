import boto3
import json
import requests

import os
import time
from datetime import datetime, timedelta

# Get the environment variables to config the APP
ns_URL = os.environ.get('NS_URL')
api_secret = os.environ.get('API_SECRET')  # this must come in SHA-1 format
app_debug = False
app_debug = os.environ.get('APP_DEBUG')


def lambda_handler(event, context):
    # The idea is:
    # 1st get the latest entry from NS
    # 2nd load and parse the JSON from S3
    # 3rd send the GS and the treatments to NS

    return {
        "statusCode": 200,
        "body": json.dumps({
            "updateBG": readDATA(event)
        })
    }


def readDATA(event):

    # Load and parse the JSON
    # from S3
    s3 = boto3.client('s3')

    try:
        obj = s3.get_object(Bucket=event['Records'][0]['s3']['bucket']
                            ['name'], Key=event['Records'][0]['s3']['object']['key'])
        data = json.loads(obj['Body'].read())
        print(obj)
    except botocore.exceptions.ClientError as error:
        # Put your error handling logic here
        raise error
    except botocore.exceptions.ParamValidationError as error:
        raise ValueError(
            'The parameters you provided are incorrect: {}'.format(error))

    if app_debug:
        print("Processing file from bucket [", event['Records'][0]['s3']['bucket']
              ['name'], "] Key [", event['Records'][0]['s3']['object']['key'], "]")

    # upload device status first
    r = requests.post(ns_URL + '/api/v1/devicestatus.json',
                      headers={
                          'api-secret': api_secret},
                      json={
                          "device": data['pumpModelNumber'],
                          "created_at": data['sMedicalDeviceTime'],
                          "loop": data['medicalDeviceFamily'],
                          "pump": {
                              "clock": data['medicalDeviceTimeAsString'],
                              "battery": {
                                  "status": data['conduitBatteryStatus'],
                                  "voltage": 1.5 * (data['medicalDeviceBatteryLevelPercent'] / 100),
                              },
                              "reservoir": data['reservoirRemainingUnits']
                          }
                      })

    # Let's check if we introduced the sensor init time
    print("Sensor duration hours (until dead):", data["sensorDurationHours"])
    checkSensorInit(data["sensorDurationHours"], data['conduitSerialNumber'])

    # Get the latest BG available on NS and start working
    latest_entry = requests.get(
        ns_URL + "/api/v1/entries.json?find[dateString][$gte]=2015-01-01&count=1")

    # add error checking
    if latest_entry.status_code == 200:
        latest_entry_json = latest_entry.json()

        #  In this case the latest entry is a timestamp not a ISO date
        if latest_entry_json:
            latest_entry_date = latest_entry_json[0]["date"]
        else:
            latest_entry_date = 1
    else:
        # error because the http endpoint is not available or throws an error
        # probably i should improve the error management
        latest_entry_date = 1

    # python convert unix timestamp to date
    if app_debug:
        print(latest_entry_date, '\n')

    # print(data)
    if app_debug:
        print("Latest SG: ", data["lastSG"]["sg"])

    # We will use error to decide if we delete or not from S3 the file to be reprocessed
    process_error = 0

    # Let's process BG first
    for glucose in data["sgs"]:
        #  Let's check if SG is 0, as if it is, then we do not have fields datetime or sensorState
        if glucose["sg"] != 0:
            calc_timestamp = time.mktime(datetime.strptime(
                glucose["datetime"], "%Y-%m-%dT%H:%M:%SZ").timetuple())
            if app_debug:
                print("Glucose: [",
                      glucose["sg"], "] at date/time: ", glucose["datetime"], " with calculated timestamp: ", calc_timestamp)
            if calc_timestamp*1000 >= latest_entry_date:
                # it's newer than the latest at NS, upload it
                # if app_debug:
                print("Uploading to NS glucose ", glucose["sg"])
                r = requests.post(ns_URL + '/api/v1/entries.json',
                                  headers={
                                      'api-secret': api_secret},
                                  json={
                                      "type": "sgv",
                                      "date": int(calc_timestamp*1000),
                                      "dateString": glucose["datetime"],
                                      "sgv": glucose["sg"]
                                  })
                if app_debug:
                    print("Status: ", r.status_code)
                if r.status_code != 200:
                    process_error = 1

    ##############################################
    # Let's process MEALS second
    ##############################################

    latest_entry = requests.get(
        ns_URL + "/api/v1/treatments.json?find[eventType]=Meal+Bolus&count=1")
    latest_entry_json = latest_entry.json()

    if latest_entry_json:
        latest_entry_date = time.mktime(datetime.strptime(
            latest_entry_json[0]["created_at"], "%Y-%m-%dT%H:%M:%S.000Z").timetuple())
    else:
        latest_entry_date = 1

    if app_debug:
        print("Returned latest MEAL created_at:", latest_entry_date)

    for markers in data["markers"]:
        # This is a MEAL entry
        if markers['type'] == 'MEAL':
            calc_timestamp = time.mktime(datetime.strptime(
                markers["dateTime"], "%Y-%m-%dT%H:%M:%SZ").timetuple())

            if int(calc_timestamp) >= latest_entry_date:
                if app_debug:
                    print("Meal: [",
                          markers["amount"], "] at date/time: ", markers["dateTime"], "/", str(int(calc_timestamp)))
                # I need to find the insulin attached to this entry
                meal_insulin = search_for_insulin(
                    data["markers"], markers["index"])

                # it's newer than the latest at NS, upload it
                # if app_debug:
                print("Uploading to NS MEAL Carbs: ",
                      markers["amount"], " with amount insulin:", meal_insulin)
                r = requests.post(ns_URL + '/api/v1/treatments.json',
                                  headers={
                                      'api-secret': api_secret},
                                  json={
                                      "eventType": "Meal Bolus",
                                      "created_at": markers["dateTime"],
                                      "carbs": markers["amount"],
                                      "insulin": meal_insulin
                                  })
                if app_debug:
                    print("Status: ", r.status_code)
                if r.status_code != 200:
                    process_error = 1

        ##############################################
        # This is a Basal Entry
        ##############################################

        latest_entry = requests.get(
            ns_URL + "/api/v1/treatments.json?find[eventType]=Temp+Basal&count=1")
        latest_entry_json = latest_entry.json()

        if latest_entry_json:
            latest_entry_date = time.mktime(datetime.strptime(
                latest_entry_json[0]["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ").timetuple())
        else:
            latest_entry_date = 1

        if app_debug:
            print("Returned latest AUTO_BASAL created_at:", latest_entry_date)

        if markers['type'] == 'AUTO_BASAL_DELIVERY':
            calc_timestamp = time.mktime(datetime.strptime(
                markers["dateTime"], "%Y-%m-%dT%H:%M:%SZ").timetuple())
            if int(calc_timestamp) >= int(latest_entry_date):
                if app_debug:
                    print("Auto_Basal: [",
                          markers["bolusAmount"], "] at date/time: ", markers["dateTime"], "/", latest_entry_date, " vs ", calc_timestamp)
                # it's newer than the latest at NS, upload it
                # if app_debug:
                print("Uploading to NS AUTO_BASAL insulin: ",
                      markers["bolusAmount"])
                r = requests.post(ns_URL + '/api/v1/treatments.json',
                                  headers={
                                      'api-secret': api_secret},
                                  json={
                                      "eventType": "Temp Basal",
                                      # I fix this to 30 as a default, as a new overlapping will kill the current one
                                      "duration": 30,
                                      "created_at": markers["dateTime"],
                                      "timestamp": markers["dateTime"],
                                      "absolute": markers["bolusAmount"],
                                      "rate": markers["bolusAmount"],
                                      "enteredBy": "CarelinkToNS",
                                      "utcOffset": 60
                                  })
                if app_debug:
                    print("Status: ", r.status_code)
                if r.status_code != 200:
                    process_error = 1

        ##############################################
        # This is a BOLUS WIZARD Entry
        ##############################################
        latest_entry = requests.get(
            ns_URL + "/api/v1/treatments.json?find[eventType]=Correction+Bolus&count=1")
        latest_entry_json = latest_entry.json()

        if latest_entry_json:
            latest_entry_date = time.mktime(datetime.strptime(
                latest_entry_json[0]["created_at"], "%Y-%m-%dT%H:%M:%S.000Z").timetuple())
        else:
            latest_entry_date = 1

        if app_debug:
            print("Returned latest CORRECTION BOLUS created_at:", latest_entry_date)

        if markers['type'] == 'INSULIN' and (markers['activationType'] == 'AUTOCORRECTION' or markers['activationType'] == 'RECOMMENDED'): 
            calc_timestamp = time.mktime(datetime.strptime(
                markers["dateTime"], "%Y-%m-%dT%H:%M:%SZ").timetuple())
            if app_debug:
                if int(calc_timestamp) >= int(latest_entry_date):
                    if app_debug:
                        print("Correction Bolus: [",
                              markers["programmedFastAmount"], "] at date/time: ", markers["dateTime"])
                    # it's newer than the latest at NS, upload it
                    # if app_debug:
                    print("Uploading to NS AUTO_CORRECTION insulin: ",
                          markers["programmedFastAmount"])
                    r = requests.post(ns_URL + '/api/v1/treatments.json',
                                      headers={
                                          'api-secret': api_secret},
                                      json={
                                          "eventType": "Correction Bolus",
                                          "duration": 0,
                                          "created_at": markers["dateTime"],
                                          "timestamp": markers["dateTime"],
                                          "insulin": markers["programmedFastAmount"],
                                          "bolus": {
                                              "amount": markers["deliveredFastAmount"],
                                              "programmed": markers["programmedFastAmount"],
                                              "unabsorbed": 0,
                                              "duration": 0
                                          },
                                          "notes": json.dumps(markers),
                                          "enteredBy": "CarelinkToNS",
                                          "utcOffset": 60
                                      })
                    if app_debug:
                        print("Status: ", r.status_code)
                    if r.status_code != 200:
                        process_error = 1

    if process_error == 0:
        # Lets delete the file from S3 bucket
        s3.delete_object(Bucket=event['Records'][0]['s3']['bucket']['name'],
                         Key=event['Records'][0]['s3']['object']['key'])
        if app_debug:
            print("File deleted from S3")
        return 200
    else:
        return 500


def search_for_insulin(sch_json, index):

    for insulin in sch_json:
        if insulin['index'] == index and insulin['type'] == 'INSULIN':
            return insulin['programmedFastAmount']
    return 0


def checkSensorInit(daysUntilExpire, uuidTXT):
    # Get latest sensor active time
    latest_entry = requests.get(
        ns_URL + "/api/v1/treatments.json?find[eventType]=Sensor+Start&count=1")
    if latest_entry.status_code == 200:
        latest_entry_json = latest_entry.json()

        if latest_entry_json:
            latest_entry_date = time.mktime(datetime.strptime(
                latest_entry_json[0]["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ").timetuple())
        else:
            latest_entry_date = 1
    else:
        # there is no data or error sending the https request
        latest_entry_date = 1

    # Calculate initialization sensor date / time
    current_date = datetime.now()
    # Medtronic Sensors G3/G4 duration is 7 days
    hours_since_initiated = (7*24) - daysUntilExpire
    init_date = current_date - timedelta(hours=hours_since_initiated)
    ts_init_date = int(datetime.timestamp(init_date))

    if app_debug:
        print("Calculated sensor init date:", str(init_date),
              "/", ts_init_date, " vs ", latest_entry_date)

    # Compare and update or not
    process_error = 0
    if latest_entry_date != ts_init_date:
        # Dates are different so sensors are different, let's insert the information
        if app_debug:
            print("New sensor inserted at: ",
                  str(init_date))
        r = requests.post(ns_URL + '/api/v1/treatments.json',
                          headers={
                              'api-secret': api_secret},
                          json={
                              "eventType": "Sensor Start",
                              "created_at": str(init_date),
                              "timestamp": ts_init_date,
                              "enteredBy": "CarelinkToNS",
                              "uuid": uuidTXT,
                              "sysTime": str(init_date)
                          })
        if app_debug:
            print("Status: ", r.status_code)
        if r.status_code != 200:
            process_error = 1

    return process_error
