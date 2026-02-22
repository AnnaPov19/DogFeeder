from machine import Pin, I2C, PWM
import time
import uasyncio
from lcd_api import LcdApi
from pico_i2c_lcd import I2cLcd
from hx711 import HX711
import urequests
import network
import ujson

#######GLOBAL#######

feed_event = uasyncio.Event()
measure_event = uasyncio.Event()
lcd_food_event = uasyncio.Event()
PUSHCUT_URL = "https://api.pushcut.io/privatecode/notifications/DogFeed"
weight_display_until = 0
WifiConnected = False
LastMeasurement = False

        
#######LCD#######
        
I2C_ADDR     = 0x27
I2C_NUM_ROWS = 2
I2C_NUM_COLS = 16

# Initialize I2C and LCD objects
i2c_lcd = I2C(1, sda=machine.Pin(2), scl=machine.Pin(3), freq=400000)
lcd = I2cLcd(i2c_lcd, I2C_ADDR, I2C_NUM_ROWS, I2C_NUM_COLS)

async def LCD_Code():
    try:
        while True:
            await lcd_food_event.wait()
            # empty at the moment
            await uasyncio.sleep(3)
            measure_event.set()
            await uasyncio.sleep(10)
            lcd_food_event.clear()
    
    except KeyboardInterrupt:
        # Turn off the display
        print("Keyboard interrupt")
        lcd.backlight_off()
        lcd.display_off()

#######WIFI#######

SSID = "YourSSID"
PASSWORD = "YourPassword"
    
def wifi_connect():
    global WifiConnected
    if WifiConnected == False:
        lcd.move_to(0, 0)
        lcd.putstr("WiFi connect....")
        i = 0
        LineFull = False
        wlan = network.WLAN(network.STA_IF)
        wlan.active(True)
        if not wlan.isconnected():
            print("Connecting to WiFi...")
            wlan.connect(SSID, PASSWORD)
            while not wlan.isconnected():
                if LineFull == False:
                    while i <= 15:
                        lcd.move_to(i, 1)
                        i = i + 1
                        lcd.putstr(".")
                        time.sleep(0.5)
                        print(".", end="")
                    i = 0
                    LineFull = True
                if LineFull == True:
                    while i <= 15:
                        lcd.move_to(i, 1)
                        i = i + 1
                        lcd.putstr(" ")
                        time.sleep(0.5)
                        print(".", end="")
                    i = 0
                    LineFull = False
                         
    print("\nConnected:", wlan.ifconfig())
    WifiConnected = True
    lcd.clear()

#######NOTIFICATION#######

def notify_pushcut(grams):
    try:
        if LastMeasurement == True:
            # URL-encode the text
            text = "Au mai ramas {}g de bobite".format(grams).replace(" ", "%20")
            url = "{}?text={}".format(PUSHCUT_URL, text)
            print("Sending Pushcut last weight:", url)
            urequests.get(url)
        else:
            # URL-encode the text
            text = "Cainele a primit {}g de bobite".format(grams).replace(" ", "%20")
            url = "{}?text={}".format(PUSHCUT_URL, text)
            print("Sending Pushcut first weight:", url)
            urequests.get(url)
    except Exception as e:
        print("Pushcut failed:", e)

#######SYSLED#######

led = Pin("LED", Pin.OUT)

async def blink():
    while True:
        led.value(1)
        await uasyncio.sleep(1)
        led.value(0)
        await uasyncio.sleep(1)
 
#######RTC#######

i2c_rtc = I2C(0, scl=Pin(1), sda=Pin(0), freq=400000)
DS3231_ADDRESS = 0x68

def bcd_to_dec(value):
    return (value >> 4) * 10 + (value & 0x0F)

async def read_time():
    while True:
        data = i2c_rtc.readfrom_mem(DS3231_ADDRESS, 0x00, 7)
        seconds = bcd_to_dec(data[0] & 0x7F)
        minutes = bcd_to_dec(data[1])
        hours   = bcd_to_dec(data[2] & 0x3F)
        day     = bcd_to_dec(data[4])
        month   = bcd_to_dec(data[5] & 0x1F)
        year    = bcd_to_dec(data[6]) + 2000
    
        print("{:02d}-{:02d}-{:04d} {:02d}:{:02d}:{:02d}".format(day, month, year, hours, minutes, seconds))
        
        # Starting at the second line (0, 1)
        if time.ticks_diff(weight_display_until, time.ticks_ms()) > 0:
            # weight is active → do NOT overwrite first line
            pass
        else:
            lcd.move_to(0, 0)
            lcd.putstr("{:02d}/{:02d}/{:04d}".format(day, month, year))
        lcd.move_to(0, 1)
        lcd.putstr("{:02d}:{:02d}:{:02d}".format(hours, minutes, seconds))  
        
        if hours in (9,18) and minutes == 15 and seconds in (0,1):
            feed_event.set()
            
        await uasyncio.sleep(2)
        
#######SERVO#######
        
# Servo signal pin
SERVO_PIN = 4

# Setup PWM for servo
servo = PWM(Pin(SERVO_PIN))
servo.freq(50)  # Standard servo frequency

# MG996R pulse width range for Pico (50Hz)
MIN_DUTY = 1638   # ~0°  (0.5 ms)
MAX_DUTY = 8192   # ~180° (2.5 ms)

def set_angle(angle):
    duty = int(MIN_DUTY + (angle / 180) * (MAX_DUTY - MIN_DUTY))
    servo.duty_u16(duty)

async def Servo90():
    while True:
        await feed_event.wait()
        # Move to 90 degrees
        set_angle(90)
        await uasyncio.sleep(0.55)

        # Move back to 0 degrees
        set_angle(0)
        await uasyncio.sleep(0.55)
        lcd_food_event.set()
        feed_event.clear()    
        
#######HX711#######

OFFSET = 7996169     # Offset after calibration was done
SCALE  = -948.79     # Scale after calibration was done
   
hx = HX711(dt=19, sck=18)

hx.offset = OFFSET
hx.set_scale(SCALE)

def read_weight_grams():
    weight = hx.get_weight(samples=10)

    # Noise is eliminated
    if abs(weight) < 5:
        return 0

    return round(weight, 1)

async def MeasureFood():
    global weight_display_until
    global LastMeasurement
    
    while True:       
        await measure_event.wait()
        print("Food measurement active...")
        first_weight = read_weight_grams()
        notify_pushcut(first_weight)

        # Show weight for 3 minutes
        start_time = time.ticks_ms()
        weight_display_until = time.ticks_add(time.ticks_ms(), 180000)
        two_min_triggered = False

        while time.ticks_diff(weight_display_until, time.ticks_ms()) > 0:
            last_weight = read_weight_grams()
            print("Weight (g):", last_weight)
            if LastMeasurement == False:
                lcd.move_to(0, 0)
                lcd.putstr("Dog was fed {0:.0f}g".format(first_weight))
            
            now = time.ticks_ms()
            elapsed = time.ticks_diff(now, start_time)

            if not two_min_triggered and elapsed >= 120000:
                two_min_triggered = True
                LastMeasurement = True
                lcd.clear()
                lcd.move_to(0, 0)
                lcd.putstr("{0:.0f}g of food left".format(last_weight))
                notify_pushcut(last_weight)

            await uasyncio.sleep(3)  # update every 3 seconds
            

        print("Measurement done")
        LastMeasurement = False
        two_min_triggered = False
        lcd.clear()
        measure_event.clear()
           
#######MAIN#######
    
async def Main():
    task1 = uasyncio.create_task(blink())
    task2 = uasyncio.create_task(MeasureFood())
    task3 = uasyncio.create_task(read_time())
    task4 = uasyncio.create_task(Servo90())
    task5 = uasyncio.create_task(LCD_Code())
    
    await uasyncio.gather(task1,task2,task3,task4,task5)

wifi_connect()
uasyncio.run(Main())