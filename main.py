# -------------------------------------------------------------- #
# Logging
logloc:bool =       False # Log location data
logwx:bool =        False # Log weather data
loggingdelay:int =  120   # Time delay between adding data to log in seconds
clearlog:bool =     True  # Clear log on startup

# API Transmitter Info
apikey:str = "146300.CJTqvO2lYIQu1w"
name:str   = "HB9TPR"

# Webserver
port:int = 8080
min_update_delay = 10 # Minimal delay between updating data

# Balloon Info ( For Prediction )
balloon_ascent:float      = 5     # ascent speed in m/s
donotchange_ascent:bool   = False # true if should not change ascent according to GPS altitude value change over time

balloon_descent:float     = 5     # descent speed in m/s
donotchange_descent:bool  = False # true if should not change descent according to GPS altitude value change over time

balloon_burst:float       = 30000 # m     relative to sea level
# -------------------------------------------------------------- #

from datetime import date
import json
from http.server import SimpleHTTPRequestHandler
from urllib import request
from urllib.parse import urlparse,parse_qs,urlencode
import socketserver
import datetime
import xml.etree.ElementTree as ET
from threading import Thread
from time import sleep  
from typing import Dict, List, Tuple, Type, Union, Optional, NoReturn
import ssl


APRSLocation = Dict[str, Union[str, float, int]]
APRSWeather  = Dict[str, str]

UP:int   =  1
DOWN:int = -1

speed_values:List[float] = [0]

diff_2h = datetime.timedelta(hours=2)


visual_for_key:Dict[str, Tuple[str, str]] = {
    "temp": ("Temperature", "{key} C"),
    "pressure": ("Pressure", "{key} mbar"),
    "humidity": ("Humidity", "{key}%"),
    "luminosity": ("Luminosity", "{key}"),
    "speed": ("Speed", "{key} km/h"),
    "course": ("Course", "{key}&deg;"),
    "wind_direction": ("Wind Direction", "{key}&deg;")
}

lastdataupdatetime = None

going:int = UP
lastalt:float = 0.0
neg_20_sec = datetime.timedelta(seconds=-20)
lasttime:datetime.datetime = datetime.datetime.now() + diff_2h + neg_20_sec

lastuuid:str = ""
lastloc:Optional[APRSLocation] =  None
lastwx:Optional[APRSLocation]  =  None

balloon_burst_mod:float = balloon_burst

jsondec:json.JSONDecoder = json.JSONDecoder()
jsonenc:json.JSONEncoder = json.JSONEncoder()

def gettransmitterinfo() -> Tuple[Optional[APRSLocation], Optional[APRSWeather]]:
    with request.urlopen(f"https://api.aprs.fi/api/get?name={name}&what=loc&apikey={apikey}&format=json") as rqst:
        encdata:bytes = rqst.read()
        try:
            enc:str = encdata.decode()
            dec:Dict[str, Union[str, int, List[APRSLocation]]] = jsondec.decode(enc)
            loc:Optional[APRSLocation]

            if dec["result"] == "ok":
                loc = dec["entries"][0]
                setlastdata("loc", loc)
            else:
                print("Failed to get aprs location:", dec["description"])
                loc = None
        except IndexError:
            print("No device match (loc)")
            loc = None
    
    with request.urlopen(f"https://api.aprs.fi/api/get?name={name}&what=wx&apikey={apikey}&format=json") as rqst:
        encdata:bytes = rqst.read()
        try:
            enc:str = encdata.decode()
            dec:Dict[str, Union[str, int, List[APRSWeather]]] = jsondec.decode(enc)
            wx:Optional[APRSWeather]
            if dec["result"] == "ok":
                wx = dec["entries"][0]
                setlastdata("wx", wx)
            else:
                print("Failed to get aprs weather data:", dec["description"])
                wx = None
        except IndexError:
            print("No device match (wx)")
            wx = None
    
    return loc,wx

def avg(l:List[float]) -> float:
    t:float = 0
    for i in l:
        t += i
    return t / len(l)

def setlastdata(key:str, value:Union[str, APRSLocation, APRSWeather]) -> None:
    f = open("lastdata.json", "r")
    dat:str = f.read()
    f.flush()
    f.close()

    dec:Dict[str, Union[APRSWeather, APRSLocation, str]] = jsondec.decode(dat)
    dec[key] = value
    dat = jsonenc.encode(dec)

    f = open("lastdata.json", "w")
    f.write(dat)
    f.flush()
    f.close()

def getprediction() -> Optional[Tuple[Dict[str, float], str, str, bool, bool, APRSLocation, APRSWeather]]:
    global lastuuid,lastloc,lastalt,going,lasttime,speed_values,balloon_ascent,balloon_descent
    loc:Optional[APRSLocation]


    loc,wx = gettransmitterinfo()

    uuid:str

    usedlastloc:bool = False
    if loc == None:
        loc = lastloc
        usedlastloc = True
    usedlastwx:bool = False
    if wx == None:
        wx = lastwx
        usedlastwx = True
    
    alt:float

    if "altitude" in loc.keys():
        alt = loc["altitude"]
    else:
        if "pressure" in wx.keys():
            alt = 44331.5 - 4946.62 * (float(wx["pressure"])*100) ** (0.190263)
            alt = 0 if alt < 0 else alt
            print("altitude not being transmitted, predicting according to pressure value",alt)
        else:
            print("altitude not being transmitted, predicting with a value of 0")
            alt = 0

    if not usedlastloc:
        timediff:datetime.timedelta = (datetime.datetime.now() + diff_2h) - lasttime

        altdiff:float  = float(alt) - lastalt

        if timediff.total_seconds() >= 20 or altdiff != 0.0:
            cmps:float = altdiff / timediff.total_seconds()

            if len(speed_values) >= 5:
                speed_values.remove(speed_values[0])
            speed_values.append(cmps)

            lastalt = float(alt)
            lasttime = datetime.datetime.now() + diff_2h
        
        mps:float = avg(speed_values)
    
        if mps > 0:
            print(f"according to GPS, balloon going up with an average speed of {mps} m/s")
            going = UP
            if not donotchange_ascent:
                balloon_ascent = mps
        elif mps < 0:
            print(f"according to GPS, balloon going down with an average speed of {mps} m/s")
            going = DOWN
            if not donotchange_descent:
                balloon_descent = -mps
    lat:float
    if "lat" in loc.keys():
        lat = loc["lat"]
    else:
        print("latitude not being transmitted, failed to predict")
        return
    
    lng:float
    if "lng" in loc.keys():
        lng = loc["lng"]
    else:
        print("longitude not being transmitted, failed to predict")
        return
    
    if not usedlastloc:
        if going == DOWN:
            balloon_burst_mod = alt - 5
        if going == UP:
            balloon_burst_mod = balloon_burst
        
        now:datetime.datetime = datetime.datetime.now() + diff_2h
        datenow:datetime.date = now.date()
        timenow:datetime.time = now.time()

        d_day:int   = datenow.day
        d_month:int = datenow.month
        d_year:int  = datenow.year

        t_hour:int =  timenow.hour
        t_min:int  =  timenow.minute
        t_sec:int  =  timenow.second

        print(t_hour, t_min, t_sec)
        
        pred_data:Dict[str, Union[str, float, int]] = {
            "launchsite":  "Other",
            "lat":         lat,
            "lon":         lng,
            "initial_alt": alt,
            "hour":        t_hour,
            "min":         t_min,
            "second":      t_sec,
            "day":         d_day,
            "month":       d_month,
            "year":        d_year,
            "ascent":      balloon_ascent,
            "burst":       balloon_burst_mod,
            "drag":        balloon_descent,
            "submit":     "Run+Prediction"
        }

        req:request.Request

        req = request.Request(f"http://predict.habhub.org/ajax.php?action=submitForm", data=bytes(urlencode(pred_data), encoding="utf-8"), method="POST")
        with request.urlopen(req) as resp:
            encdata:bytes = resp.read()
            pred_submit:Dict[str, Union[str, int]] = jsondec.decode(encdata.decode())
            if pred_submit["valid"] == "true":
                uuid = pred_submit["uuid"]
                print(uuid)
                setlastdata("uuid", uuid)
            else:
                error:str = pred_submit["error"]
                print(f"Prediction invalid: {error}")
                return
        
        gcontext:ssl.SSLContext = ssl.SSLContext()

        req = request.Request(f"https://predict.habhub.org/preds/{uuid}/progress.json", method="GET")
        with request.urlopen(req, context=gcontext) as resp:
            encdata:bytes = resp.read()
            pred_progress:Dict[str, Union[bool, str, List[str]]] = jsondec.decode(encdata.decode())
        while not pred_progress["pred_complete"]:
            if pred_progress["error"] != "":
                error:str = pred_progress["error"]
                print(f"Gathering progress data failed: {error}")
                return
            req = request.Request(f"https://predict.habhub.org/preds/{uuid}/progress.json", method="GET")
            with request.urlopen(req) as resp:
                encdata = resp.read()
                pred_progress = jsondec.decode(encdata.decode())
        

        req = request.Request(f"https://predict.habhub.org/kml.php?uuid={uuid}", method="GET")
        with request.urlopen(req, context=gcontext) as resp:
            encdata:bytes = resp.read()
            kml:str = encdata.decode()
        
        with open("plan.kml", "w") as plan:
            plan.write(kml)
            plan.flush()
            plan.close()
    
    if usedlastloc:
        with open("plan.kml", "r") as plan:
            kml = plan.read()
        uuid = lastuuid
    
    lastuuid = uuid
    
    return {"lat": lat, "lon": lng, "alt": alt},kml,uuid,usedlastloc,usedlastwx,loc,wx


class ReqHandler(SimpleHTTPRequestHandler):
    path:str
    def do_GET(self) -> None:
        global lastdataupdatetime
        self.send_response(200)
        self.send_header("Content-type", "text/html")

        self.end_headers()

        query_components = parse_qs(urlparse(self.path).query)

        if self.path == "/":
            now = datetime.datetime.now() + diff_2h

            if lastdataupdatetime == None or (now - lastdataupdatetime).total_seconds() >= min_update_delay:
                lastdataupdatetime = now
                data:Dict[str, float]
                kml:str
                uuid:str
                usedlastloc:bool
                usedlastwx:bool
                loc:Optional[APRSLocation]
                wx:Optional[APRSWeather]
                data,kml,uuid,usedlastloc,usedlastwx,loc,wx = getprediction()
                with open("index.html", "r") as page:
                    dat:str
                    dat = page.read()
                    dat = dat.replace("CENTER_LAT", str(data["lat"]))
                    dat = dat.replace("CENTER_LNG", str(data["lon"]))
                    dat = dat.replace("PLAN_KML_UUID", uuid)
                    if usedlastloc:
                        dat = dat.replace("WARNING_HIDDEN", "")
                    else:
                        dat = dat.replace("WARNING_HIDDEN", "hidden")
                    
                    html:str = """
                            <tr>
                                <th><span class="senstext">{name}</span></th>
                                <th><span class="sensvalue">{value}</span></th>
                            </tr>"""
                    fhtml:str = ""
                    
                    alt:float = 0.0
                    alt = data["alt"]
                    
                    pressureadded:bool = False
                    m:Dict[str, str]
                    for s in {**loc, **wx}:
                        try:
                            v:str = wx[s]
                        except KeyError:
                            v:str = loc[s]
                        try:
                            nm:str
                            sfx:str
                            nm,sfx = visual_for_key[s]
                            m = {"name": nm, "value": sfx.format_map(dict(key=str(v)))}
                            fhtml += html.format_map(m)
                            if s == "pressure":
                                pressureadded = True
                                m = {"name": "Pressure (Altitude)", "value": str(int((101325 * (1 - 2.25577 * 10**-5 * alt) ** 5.25588) / 100)) + " mbar"}
                                fhtml += html.format_map(m)
                        except KeyError:pass
                    if False:#not pressureadded:
                        m = {"name": "Pressure (Altitude)", "value": str( (101325 * (1 - 2.25577 * 10**-5 * alt) ** 5.25588) / 100 ) + " mbar"}
                        fhtml += html.format_map(m)
                    m = {"name": "Altitude", "value": str(alt)}
                    fhtml += html.format_map(m)
                    dat = dat.replace("SENSOR_VALUES", fhtml)

                    root:ET.Element = ET.fromstring(kml)
                    doc:ET.Element = list(root)[0]
                    for elem in list(doc):
                        for elem2 in list(elem):
                            if elem2.text == "Predicted Balloon Landing":
                                for elem3 in list(elem):
                                    if elem3.tag.endswith("Point"):
                                        for elem4 in list(elem3):
                                            if elem4.tag.endswith("coordinates"):
                                                p:ET.Element = elem4
                                                spl:List[str] = p.text.split(",")
                                                for i in range(len(spl)):
                                                    if i == 0:
                                                        dat = dat.replace("DEST_LAT", spl[i])
                                                    elif i == 1:
                                                        dat = dat.replace("DEST_LNG", spl[i])
                                                        break
                                    elif elem3.tag.endswith("description"):
                                        dt:str = elem3.text.split("at ")[2].replace(".", "")
                                        dat = dat.replace("DEST_DATETIME", dt)
                        

                    self.wfile.write(bytes(dat, "utf8"))
                    with open("lastindex.html", "w") as f:
                        f.write(dat)
                        f.flush()
                        f.close()
            else:
                with open("lastindex.html", "r") as f:
                    dat = f.read()
                    self.wfile.write(bytes(dat, "utf8"))
            return
        
        if self.path == "/plan.kml":
            
            with open("plan.kml", "r") as page:
                dat = page.read()
                
                
                root = ET.fromstring(dat)
                tree = ET.parse("plan.kml")
                root = tree.getroot()
                doc = list(root)[0]
                for elem in list(doc):
                    for elem2 in list(elem):
                        if elem2.text == "Balloon Burst":
                            if going == DOWN:
                                doc.remove(elem)
                            for elem3 in list(elem):
                                if elem3.tag.endswith("Point"):
                                    for elem4 in list(elem3):
                                        if elem4.tag.endswith("coordinates"):
                                            spl = elem4.text.split(",")
                                            ot = ""
                                            for i in range(len(spl)):
                                                ot += spl[i]
                                                if i == 0:
                                                    ot += ","
                                                if i == 1:
                                                    break
                                            elem4.text = ot


                ET.register_namespace('', "http://www.opengis.net/kml/2.2")
                dat = ET.tostring(root)
                tree.write("plan.kml")

                try:
                    self.wfile.write(bytes(dat, "utf-8"))
                except TypeError:
                    self.wfile.write(dat)
            return
        
        if self.path == "/manualcoords":
            with open("manualcoords.html", "r") as page:
                self.wfile.write(bytes(page.read(), "utf-8"))
            return
        
        if self.path.startswith("/submitcoords"):
            try:
                lat = query_components["lat"]
                lng = query_components["lng"]
            except KeyError:
                print("Invalid query components:", query_components)
                return
            try:
                lat = float(lat)
                lng = float(lng)
            except:
                print("Invalid query components:", query_components)
                return

            
            f = open("lastdata.json", "r")
            dat:str = f.read()
            f.flush()
            f.close()

            datadec = jsondec.decode(dat)

            datadec["loc"]["lat"] = str(lat)
            datadec["loc"]["lng"] = str(lng)
            
            dat = jsonenc.encode(datadec)

            logdata(datadec["loc"], None)

            f = open("lastdata.json", "w")
            f.write(dat)
            f.flush()
            f.close()




f = open("lastdata.json", "r")
dat:str = f.read()
f.flush()
f.close()
dec:Dict[str, Union[APRSWeather, APRSLocation, str]] = jsondec.decode(dat)
lastloc =  dec["loc"]
lastwx =   dec["wx"]
lastuuid = dec["uuid"]


def logdata(loc,wx) -> None:
    f = open("logfile.json", "r")
    dat = f.read()
    f.flush()
    f.close()

    dec = jsondec.decode(dat)
    apnd = [loc,wx] if logloc and logwx else [loc] if logloc and not logwx else [wx] if logwx and not logloc else []
    dec.append(apnd)
    dat = jsonenc.encode(dec)

    f = open("logfile.json", "w")
    f.write(dat)
    f.flush()
    f.close()

def logger():
    while True:
        sleep(loggingdelay)
        loc,wx = gettransmitterinfo()
        logdata(loc,wx)
        


f = open("logfile.json", "w")
f.write("[]")
f.flush()
f.close()


t = Thread(target=logger)
t.daemon = True
if logloc or logwx:
    t.start()


handler:ReqHandler = ReqHandler
with socketserver.TCPServer(("", port), handler) as httpd:
    print("serving at port", port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


