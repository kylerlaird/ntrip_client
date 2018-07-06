#!/usr/bin/python -u

import hashlib

"""
2017 Kyler Laird

This is heavily based on https://github.com/jcmb/NTRIP/blob/master/NTRIP%20Client/NtripClient.py
which is heavily based on the NtripPerlClient program written by BKG
which is heavily based on a unavco original.

Open Source for the win.
"""

import rospy
import sensor_msgs.msg
import std_msgs.msg
#import threading
import multiprocessing
import Queue

import socket
import sys
import datetime
import base64
import time
from optparse import OptionParser

import serial

version=0.1 
useragent="NTRIP UnavcoPythonClient/%.1f" % version

# reconnect parameter (fixed values):
reconnectstarttime=10.0
factor=0.02
ramptime1=500.0
ramptime2=20000.0
reconnectendtime=400.0
maxReconnect=10000

def log(message):
	print '**************', message

class Unbuffered:
	"""
	Python buffers by default.  You can 
	run python with the -u switch to avoid this, but that's
	a bit onerous.  Instead, we'll wrap stdout with this.
	See: http://stackoverflow.com/questions/107705/python-output-buffering
	"""
	def __init__(self, stream):
		self.stream = stream
	def write(self, data):
		self.stream.write(data)
		self.stream.flush()
	def __getattr__(self, attr):
		return getattr(self.stream, attr)


class NtripClient(object):
	def __init__(
		self, 
		rtcm,
		buffer, 
		user,
		port, 
		caster,
		mountpoint,
		lat,
		lon,
		fix,
		out=Unbuffered(sys.stdout), 
	):
		self.buffer=buffer
		self.user=base64.b64encode(user)

		self.out=out
		self.port=port
		self.caster=caster
		self.mountpoint=mountpoint
		
		self.socket=None
		self.fix = fix
		self.rtcm = rtcm
		self.lat = lat
		self.lon = lon

		self.fix_queue = Queue.Queue()

		self.rtcm_pub = rospy.Publisher(self.rtcm, std_msgs.msg.String, queue_size=10)
		self.ros_process = multiprocessing.Process(target=rospy.spin, args=())
		self.ros_process.start()	


	def get_fix(self):
		fix_sub = rospy.Subscriber(self.fix, sensor_msgs.msg.NavSatFix, self.handle_fix)
		#self.fix_queue.queue.clear()

		print('waiting for fix')
		fix = self.fix_queue.get()
		#print('unregister')
		#fix_sub.unregister()
		return(fix)

	def handle_fix(self, fix):
		#print('fix:', fix)
		self.fix_queue.put(fix)

	def setPosition(self, lat, lon):
		self.flagN="N"
		self.flagE="E"
		if lon>180:
			lon=(lon-360)*-1
			self.flagE="W"
		elif (lon<0 and lon>= -180):
			lon=lon*-1
			self.flagE="W"
		elif lon<-180:
			lon=lon+360
			self.flagE="E"
		else:
			self.lon=lon
		if lat<0:
			lat=lat*-1
			self.flagN="S"
		self.lonDeg=int(lon)
		self.latDeg=int(lat)
		self.lonMin=(lon-self.lonDeg)*60
		self.latMin=(lat-self.latDeg)*60

	def getMountPointString(self):
		mountPointString = "GET /%s HTTP/1.0\r\nUser-Agent: %s\r\nAuthorization: Basic %s\r\n\r\n" % (self.mountpoint, useragent, self.user)
		#print mountPointString
		return mountPointString
	
	def getGGAString(self):
		# Kyler
		#return '$GPGGA,142108.80,4053.3076074,N,08711.5919956,W,1,19,0.6,206.210,M,-34.073,M,0.0,*73'

		now = datetime.datetime.utcnow()
		ggaString= "GPGGA,%02d%02d%04.2f,%02d%011.8f,%1s,%03d%011.8f,%1s,1,05,0.19,+00400,M,47.950,M,," % \
			(now.hour,now.minute,now.second,self.latDeg,self.latMin,self.flagN,self.lonDeg,self.lonMin,self.flagE)
		checksum = self.calcultateCheckSum(ggaString)
		return "$%s*%s\r\n" % (ggaString, checksum)
		
	def calcultateCheckSum(self, stringToCheck):
		xsum_calc = 0
		for char in stringToCheck:
			xsum_calc = xsum_calc ^ ord(char)
		return "%02X" % xsum_calc
		
	def readData(self):
		reconnectTry=0
		sleepTime=0
		reconnectTime=0

		print('connect')
		if self.fix:
			fix = self.get_fix()
			(self.lat, self.lon) = (fix.latitude, fix.longitude)

		self.setPosition(self.lat, self.lon)

		print 'connecting...'

		self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		error_indicator = self.socket.connect_ex((self.caster, self.port))
		self.socket.settimeout(5)
		if error_indicator==0:
			self.socket.sendall(self.getMountPointString())
			try:
				casterResponse=self.socket.recv(1024)
			except socket.timeout:
				return

			print casterResponse.strip()
			#sys.stdout.write(casterResponse)
			#print '.'
			#receiver.write(casterResponse)

			if casterResponse.find("SOURCETABLE")>=0:
				log("Mount point does not exist")
				log("Writing sourcetable.dat")
				f = open("sourcetable.dat", "w")
				data=""
				#while self.socket:
				while not data.find("ENDSOURCETABLE")>=0:
					data = self.socket.recv(50)
					f.write(data)
				f.close()
				self.socket.close()
				self.socket=None
				sys.exit(1)
			elif casterResponse.find("401 Unauthorized")>=0:
				log("Unauthorized request")
				sys.exit(1)
			elif casterResponse.find("ICY 200 OK")>=0:
				#Request was valid
				gga = self.getGGAString()
				print gga
				self.socket.sendall(gga)
			data = "Initial data"
			while data:
				try:
					data=self.socket.recv(self.buffer)
				except:
					print '*********** problem ***********'
					return()

		
			
				self.rtcm_pub.publish(data)
				#print(len(data))
				#print(len(data), hashlib.sha224(data).hexdigest())

				#self.out.write(data)
				#print time.time()
				#print receiver_rtcm.write(data)

			self.socket.close()
			self.socket=None
		else:
			reconnectTry += 1
			sleepTime += reconnectTime
			if sleepTime<=ramptime1:
				reconnectTime=reconnectstarttime
			if sleepTime>ramptime2:
				reconnectTime=reconnectendtime
			if (sleepTime>ramptime1) and (sleepTime<=ramptime2):
				reconnectTime=factor*sleepTime
			self.socket=None
			log("%s No Connection to NtripCaster.  Trying again in %f seconds" % (datetime.datetime.now(), sleepTime))
			time.sleep(5)


if __name__ == '__main__':
	rospy.init_node('ntrip_client', anonymous=True)

	ntripArgs = {
		'buffer': int(rospy.get_param('~buffer', 50)),
		'lat': rospy.get_param('~lat', None),
		'lon': rospy.get_param('~lon', None),
		'user': rospy.get_param('~user', None),
		'mountpoint': rospy.get_param('~mountpoint', ''),
		'caster': rospy.get_param('~caster'),
		'port': int(rospy.get_param('~port')),
		'rtcm': rospy.get_param('~rtcm'),
		'fix': rospy.get_param('~fix', None),
	}

	fileOutput=False
	n = NtripClient(**ntripArgs)
	while not rospy.is_shutdown():
		n.readData()
		time.sleep(1)

