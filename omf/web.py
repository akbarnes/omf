''' Web server for model-oriented OMF interface. '''

from flask import (Flask, send_from_directory, request, redirect, render_template, session, abort, jsonify, url_for)
from jinja2 import Template
from multiprocessing import Process
from passlib.hash import pbkdf2_sha512
import json, os, flask_login, hashlib, random, time, datetime as dt, shutil, boto.ses, csv, sys, platform
try:
	import fcntl
except:
	#We're on windows, where we don't support file locking.
	fcntl = type('', (), {})()
	def flock(fd, op):
		return
	fcntl.flock = flock
	(fcntl.LOCK_EX, fcntl.LOCK_SH, fcntl.LOCK_UN) = (None, None, None)
import models, feeder, network, milToGridlab, cymeToGridlab, signal, weather, anonymization
import omf
from omf.calibrate import omfCalibrate
from omf.omfStats import genAllImages
from omf.loadModelingAmi import writeNewGlmAndPlayers
from flask_compress import Compress

app = Flask("web")
Compress(app)
URL = "http://www.omf.coop"
_omfDir = os.path.dirname(os.path.abspath(__file__))

###################################################
# HELPER FUNCTIONS
###################################################

def safeListdir(path):
	''' Helper function that returns [] for dirs that don't exist. Otherwise new users can cause exceptions. '''
	try: return [x for x in os.listdir(path) if not x.startswith(".")]
	except:	return []

def getDataNames():
	''' Query the OMF datastore to get names of all objects.'''
	try:
		currUser = User.cu()
	except:
		currUser = "public"
	climates = [x[:-5] for x in safeListdir("./data/Climate/")]
	feeders = []
	for (dirpath, dirnames, filenames) in os.walk(os.path.join(_omfDir, "data","Model", currUser)):
		for fname in filenames:
			if fname.endswith('.omd') and fname != 'feeder.omd':
				feeders.append({'name': fname[:-4], 'model': dirpath.split('/')[-1]})
	networks = []
	for (dirpath, dirnames, filenames) in os.walk(os.path.join(_omfDir, "scratch","transmission", "outData")):
		for fname in filenames:
			if fname.endswith('.omt') and fname != 'feeder.omt':
				networks.append({'name': fname[:-4], 'model': 'DRPOWER'})
	# Public feeders too.
	publicFeeders = []
	for (dirpath, dirnames, filenames) in os.walk(os.path.join(_omfDir, "static","publicFeeders")):
		for fname in filenames:
			if fname.endswith('.omd') and fname != 'feeder.omd':
				publicFeeders.append({'name': fname[:-4], 'model': dirpath.split('/')[-1]})
	return {"climates":sorted(climates), "feeders":feeders, "networks":networks, "publicFeeders":publicFeeders, "currentUser":currUser}

# @app.before_request
# def csrf_protect():
# 	pass
	## NOTE: when we fix csrf validation this needs to be uncommented.
	# if request.method == "POST":
	#	token = session.get("_csrf_token", None)
	#	if not token or token != request.form.get("_csrf_token"):
	#		abort(403)

###################################################
# AUTHENTICATION AND USER FUNCTIONS
###################################################

class User:
	def __init__(self, jsonBlob): self.username = jsonBlob["username"]
	# Required flask_login functions.
	def is_admin(self): return self.username == "admin"
	def get_id(self): return self.username
	def is_authenticated(self): return True
	def is_active(self): return True
	def is_anonymous(self): return False
	@classmethod
	def cu(self):
		"""Returns current user's username"""
		return flask_login.current_user.username

def cryptoRandomString():
	''' Generate a cryptographically secure random string for signing/encrypting cookies. '''
	if 'COOKIE_KEY' in globals():
		return COOKIE_KEY
	else:
		return hashlib.md5(str(random.random())+str(time.time())).hexdigest()

login_manager = flask_login.LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login_page"
app.secret_key = cryptoRandomString()

def send_link(email, message, u={}):
	''' Send message to email using Amazon SES. '''
	try:
		key = open("emailCredentials.key").read()
		c = boto.ses.connect_to_region("us-east-1",
			aws_access_key_id="AKIAJLART4NXGCNFEJIQ",
			aws_secret_access_key=key)
		reg_key = hashlib.md5(str(time.time())+str(random.random())).hexdigest()
		u["reg_key"] = reg_key
		u["timestamp"] = dt.datetime.strftime(dt.datetime.now(), format="%c")
		u["registered"] = False
		u["email"] = email
		outDict = c.send_email("admin@omf.coop", "OMF Registration Link",
			message.replace("reg_link", URL+"/register/"+email+"/"+reg_key), [email])
		json.dump(u, open("data/User/"+email+".json", "w"), indent=4)
		return "Success"
	except:
		return "Failed"

@login_manager.user_loader
def load_user(username):
	''' Required by flask_login to return instance of the current user '''
	return User(json.load(open("./data/User/" + username + ".json")))

def generate_csrf_token():
	if "_csrf_token" not in session:
		session["_csrf_token"] = cryptoRandomString()
	return session["_csrf_token"]

app.jinja_env.globals["csrf_token"] = generate_csrf_token

@app.route("/login", methods = ["POST"])
def login():
	''' Authenticate a user and send them to the URL they requested. '''
	username, password, remember = map(request.form.get, ["username",
		"password", "remember"])
	userJson = None
	for u in safeListdir("./data/User/"):
		if u.lower() == username.lower() + ".json":
			userJson = json.load(open("./data/User/" + u))
			break
	if userJson and pbkdf2_sha512.verify(password,
			userJson["password_digest"]):
		user = User(userJson)
		flask_login.login_user(user, remember = remember == "on")
	nextUrl = str(request.form.get("next","/"))
	return redirect(nextUrl)

@app.route("/login_page")
def login_page():
	nextUrl = str(request.args.get("next","/"))
	if flask_login.current_user.is_authenticated():
		return redirect(nextUrl)
	return render_template("clusterLogin.html", next=nextUrl)

@app.route("/logout")
def logout():
	flask_login.logout_user()
	return redirect("/")

@app.route("/deleteUser", methods=["POST"])
@flask_login.login_required
def deleteUser():
	if User.cu() != "admin":
		return "You are not authorized to delete users"
	username = request.form.get("username")
	# Clean up user data.
	try:
		shutil.rmtree("data/Model/" + username)
	except Exception, e:
		print "USER DATA DELETION FAILED FOR", e
	os.remove("data/User/" + username + ".json")
	print "SUCCESFULLY DELETE USER", username
	return "Success"

@app.route("/new_user", methods=["POST"])
def new_user():
	email = request.form.get("email")
	if email == "": return "EMPTY"
	if email in [f[0:-5] for f in safeListdir("data/User")]:
		u = json.load(open("data/User/" + email + ".json"))
		if u.get("password_digest") or not request.form.get("resend"):
			return "Already Exists"
	message = "Click the link below to register your account for the OMF.  This link will expire in 24 hours:\n\nreg_link"
	return send_link(email, message)

@app.route("/forgotPassword/<email>", methods=["GET"])
def forgotpwd(email):
	try:
		user = json.load(open("data/User/" + email + ".json"))
		message = "Click the link below to reset your password for the OMF.  This link will expire in 24 hours.\n\nreg_link"
		code = send_link(email, message, user)
		if code is "Success":
			return "We have sent a password reset link to " + email
		else:
			raise Exception
	except Exception, e:
		print "ERROR: failed to password reset user", email, "with exception", e
		return "We do not have a record of a user with that email address. Please click back and create an account."

@app.route("/fastNewUser/<email>")
def fastNewUser(email):
	''' Create a new user, email them their password, and immediately create a new model for them.'''
	if email in [f[0:-5] for f in safeListdir("data/User")]:
		return "User with email {} already exists. Please log in or go back and use the 'Forgot Password' link. Or use a different email address.".format(email)
	else:
		randomPass = ''.join([random.choice('abcdefghijklmnopqrstuvwxyz') for x in range(15)])
		user = {"username":email, "password_digest":pbkdf2_sha512.encrypt(randomPass)}
		flask_login.login_user(User(user))
		with open("data/User/"+user["username"]+".json","w") as outFile:
			json.dump(user, outFile, indent=4)
		message = "Thank you for registering an account on OMF.coop.\n\nYour password is: " + randomPass + "\n\n You can change this password after logging in."
		key = open("emailCredentials.key").read()
		c = boto.ses.connect_to_region("us-east-1", aws_access_key_id="AKIAJLART4NXGCNFEJIQ", aws_secret_access_key=key)
		mailResult = c.send_email("admin@omf.coop", "OMF.coop User Account", message, [email])
		nextUrl = str(request.args.get("next","/"))
		return redirect(nextUrl)

@app.route("/register/<email>/<reg_key>", methods=["GET", "POST"])
def register(email, reg_key):
	if flask_login.current_user.is_authenticated():
		return redirect("/")
	try:
		user = json.load(open("data/User/" + email + ".json"))
	except Exception:
		user = None
	if not (user and
			reg_key == user.get("reg_key") and
			user.get("timestamp") and
			dt.timedelta(1) > dt.datetime.now() - dt.datetime.strptime(user.get("timestamp"), "%c")):
		return "This page either expired, or you are not supposed to access it. It might not even exist"
	if request.method == "GET":
		return render_template("register.html", email=email)
	password, confirm_password = map(request.form.get, ["password", "confirm_password"])
	if password == confirm_password and request.form.get("legalAccepted","") == "on":
		user["username"] = email
		user["password_digest"] = pbkdf2_sha512.encrypt(password)
		flask_login.login_user(User(user))
		with open("data/User/"+user["username"]+".json","w") as outFile:
			json.dump(user, outFile, indent=4)
	else:
		return "Passwords must both match and you must accept the Terms of Use and Privacy Policy. Please go back and try again."
	return redirect("/")

@app.route("/changepwd", methods=["POST"])
@flask_login.login_required
def changepwd():
	old_pwd, new_pwd, conf_pwd = map(request.form.get, ["old_pwd", "new_pwd", "conf_pwd"])
	user = json.load(open("./data/User/" + User.cu() + ".json"))
	if pbkdf2_sha512.verify(old_pwd, user["password_digest"]):
		if new_pwd == conf_pwd:
			user["password_digest"] = pbkdf2_sha512.encrypt(new_pwd)
			with open("./data/User/" + User.cu() + ".json","w") as outFile:
				json.dump(user, outFile, indent=4)
			return "Success"
		else:
			return "not_match"
	else:
		return "not_auth"

@app.route("/adminControls")
@flask_login.login_required
def adminControls():
	''' Render admin controls. '''
	if User.cu() != "admin":
		return redirect("/")
	users = [{"username":f[0:-5]} for f in safeListdir("data/User")
		if f not in ["admin.json","public.json"]]
	for user in users:
		userDict = json.load(open("data/User/" + user["username"] + ".json"))
		tStamp = userDict.get("timestamp","")
		if userDict.get("password_digest"):
			user["status"] = "Registered"
		elif dt.timedelta(1) > dt.datetime.now() - dt.datetime.strptime(tStamp, "%c"):
			user["status"] = "emailSent"
		else:
			user["status"] = "emailExpired"
	return render_template("adminControls.html", users = users)

@app.route("/omfStats")
@flask_login.login_required
def omfStats():
	'''Render log visualizations.'''
	if User.cu() != "admin":
		return redirect("/")
	return render_template("omfStats.html")

@app.route("/regenOmfStats")
@flask_login.login_required
def regenOmfStats():
	'''Regenarate stats images.'''
	if User.cu() != "admin":
		return redirect("/")
	genImagesProc = Process(target=genAllImages, args=[])
	genImagesProc.start()
	return redirect("/omfStats")

@app.route("/myaccount")
@flask_login.login_required
def myaccount():
	''' Render account info for any user. '''
	return render_template("myaccount.html", user=User.cu())

@app.route("/robots.txt")
def static_from_root():
	return send_from_directory(app.static_folder, request.path[1:])

###################################################
# MODEL FUNCTIONS
###################################################

@app.route("/model/<owner>/<modelName>")
@flask_login.login_required
def showModel(owner, modelName):
	''' Render a model template with saved data. '''
	if owner==User.cu() or "admin"==User.cu() or owner=="public":
		modelDir = "./data/Model/" + owner + "/" + modelName
		with open(modelDir + "/allInputData.json") as inJson:
			modelType = json.load(inJson).get("modelType","")
		thisModel = getattr(models, modelType)
		return thisModel.renderTemplate(modelDir, absolutePaths=False, datastoreNames=getDataNames())
	else:
		return redirect("/")

@app.route("/newModel/<modelType>/<modelName>", methods=["POST","GET"])
@flask_login.login_required
def newModel(modelType, modelName):
	''' Create a new model with given name. '''
	modelDir = os.path.join(_omfDir, "data", "Model", User.cu(), modelName)
	thisModel = getattr(models, modelType)
	thisModel.new(modelDir)
	return redirect("/model/" + User.cu() + "/" + modelName)

@app.route("/runModel/", methods=["POST"])
@flask_login.login_required
def runModel():
	''' Start a model running and redirect to its running screen. '''
	pData = request.form.to_dict()
	modelModule = getattr(models, pData["modelType"])
	# Handle the user.
	if User.cu() == "admin" and pData["user"] == "public":
		user = "public"
	elif User.cu() == "admin" and pData["user"] != "public" and pData["user"] != "":
		user = pData["user"].replace('/','')
	else:
		user = User.cu()
	del pData["user"]
	# Handle the model name.
	modelName = pData["modelName"]
	del pData["modelName"]
	modelDir = os.path.join(_omfDir, "data", "Model", user, modelName)
	# Update the input file.
	with open(os.path.join(modelDir, "allInputData.json"),"w") as inputFile:
		json.dump(pData, inputFile, indent = 4)
	# Run and return.
	modelModule.run(modelDir)
	return redirect("/model/" + user + "/" + modelName)

@app.route("/cancelModel/", methods=["POST"])
@flask_login.login_required
def cancelModel():
	''' Cancel an already running model. '''
	pData = request.form.to_dict()
	modelModule = getattr(models, pData["modelType"])
	modelModule.cancel(os.path.join(_omfDir,"data","Model",pData["user"],pData["modelName"]))
	return redirect("/model/" + pData["user"] + "/" + pData["modelName"])

@app.route("/duplicateModel/<owner>/<modelName>/", methods=["POST"])
@flask_login.login_required
def duplicateModel(owner, modelName):
	newName = request.form.get("newName","")
	if owner==User.cu() or "admin"==User.cu() or "public"==owner:
		destinationPath = "./data/Model/" + User.cu() + "/" + newName
		shutil.copytree("./data/Model/" + owner + "/" + modelName, destinationPath)
		with open(destinationPath + "/allInputData.json","r") as inFile:
			inData = json.load(inFile)
		inData["created"] = str(dt.datetime.now())
		with open(destinationPath + "/allInputData.json","w") as outFile:
			json.dump(inData, outFile, indent=4)
		return redirect("/model/" + User.cu() + "/" + newName)
	else:
		return False

@app.route("/publishModel/<owner>/<modelName>/", methods=["POST"])
@flask_login.login_required
def publishModel(owner, modelName):
	newName = request.form.get("newName","")
	if owner==User.cu() or "admin"==User.cu():
		destinationPath = "./data/Model/public/" + newName
		shutil.copytree("./data/Model/" + owner + "/" + modelName, destinationPath)
		with open(destinationPath + "/allInputData.json","r+") as inFile:
			inData = json.load(inFile)
			inData["created"] = str(dt.datetime.now())
			inFile.seek(0)
			json.dump(inData, inFile, indent=4)
		return redirect("/model/public/" + newName)
	else:
		return False

###################################################
# FEEDER FUNCTIONS
###################################################

def writeToInput(workDir, entry, key):
	try:
		with open(workDir + "/allInputData.json") as inJson:
			fcntl.flock(inJson, fcntl.LOCK_SH)
			allInput = json.load(inJson)
			fcntl.flock(inJson, fcntl.LOCK_UN)
		allInput[key] = entry
		with open(workDir+"/allInputData.json","r+") as inputFile:
			fcntl.flock(inputFile, fcntl.LOCK_EX)
			inputFile.truncate()
			json.dump(allInput, inputFile, indent=4)
			fcntl.flock(inputFile, fcntl.LOCK_UN)
	except:
		return "Failed"

@app.route("/gridEdit/<owner>/<modelName>/<feederNum>")
@flask_login.login_required
def feederGet(owner, modelName, feederNum):
	''' Editing interface for feeders. '''
	allData = getDataNames()
	yourFeeders = allData["feeders"]
	publicFeeders = allData["publicFeeders"]
	modelDir = os.path.join(_omfDir, "data","Model", owner, modelName)
	feederName = json.load(open(modelDir + "/allInputData.json")).get('feederName'+str(feederNum))
	# MAYBEFIX: fix modelFeeder
	return render_template(
		"gridEdit.html", feeders=yourFeeders, publicFeeders=publicFeeders, modelName=modelName, feederName=feederName,
		feederNum=feederNum, ref=request.referrer, is_admin=User.cu()=="admin", modelFeeder=False,
		public=owner=="public", currUser=User.cu(), owner=owner
	)

@app.route("/network/<owner>/<modelName>/<networkNum>")
@flask_login.login_required
def networkGet(owner, modelName, networkNum):
	''' Editing interface for networks. '''
	allData = getDataNames()
	yourNetworks = allData["networks"]
	publicNetworks = allData["networks"]
	modelDir = os.path.join(_omfDir, "data","Model", owner, modelName)
	networkName = json.load(open(modelDir + "/allInputData.json")).get('networkName1')
	networkPath = modelDir + "/" + networkName + ".omt"
	with open(modelDir + "/" + networkName + ".omt", "r") as netFile:
		networkData = json.dumps(json.load(netFile))
	#Currently unused template variables: networks, publicNetworks, currUser, 
	return render_template("transEdit.html", networks=yourNetworks, publicNetworks=publicNetworks, modelName=modelName, networkData=networkData, networkName=networkName, networkNum=networkNum, ref=request.referrer, is_admin=User.cu()=="admin", public=owner=="public",
		currUser=User.cu(), owner=owner)


@app.route("/feeder/<owner>/<model_name>/<feeder_num>/test")
@app.route("/feeder/<owner>/<model_name>/<feeder_num>")
@flask_login.login_required
def distribution_get(owner, model_name, feeder_num):
	"""Render the editing interface for distribution networks.
	"""
	model_dir = os.path.join(_omfDir, "data","Model", owner, model_name)
	with open(model_dir + "/allInputData.json", "r") as json_file:
		fcntl.flock(json_file, fcntl.LOCK_SH)
		feeder_dict = json.load(json_file)
		fcntl.flock(json_file, fcntl.LOCK_UN)
	feeder_name = feeder_dict.get('feederName' + str(feeder_num))
	feeder_file = model_dir + "/" + feeder_name + ".omd"
	with open(feeder_file, "r") as data_file:
		fcntl.flock(data_file, fcntl.LOCK_SH)
		data = json.load(data_file)
		fcntl.flock(data_file, fcntl.LOCK_UN)
	passed_data = json.dumps(data)
	component_json = get_components()
	jasmine = spec = None
	if request.path.endswith("/test") and User.cu() == "admin":
		tests = load_test_files(["distNetVizSpec.js"])
		jasmine = tests["jasmine"]
		spec = tests["spec"]
	all_data = getDataNames()
	user_feeders = all_data["feeders"]
	# Must get rid of the 'u' for unicode strings before passing the strings to JavaScript
	for dictionary in user_feeders:
		dictionary['model'] = str(dictionary['model'])
		dictionary['name'] = str(dictionary['name'])
	public_feeders = all_data["publicFeeders"]
	show_file_menu = User.cu() == "admin" or owner != "public"
	current_user = User.cu()
	return render_template(
		"distNetViz.html", thisFeederData=passed_data, thisFeederName=feeder_name, thisFeederNum=feeder_num,
		thisModelName=model_name, thisOwner=owner, components=component_json, jasmine=jasmine, spec=spec,
		publicFeeders=public_feeders, userFeeders=user_feeders, showFileMenu=show_file_menu, currentUser=current_user
	)


def load_test_files(file_names):
	"""Load the JavaScript unit-test files into a string and return the string"""
	with open(os.path.join(_omfDir, "static", "lib", "jasmine-3.3.0", "scriptTags.html"), "r") as f:
		jasmine = f.read()
	spec = ""
	for name in file_names:
		with open(os.path.join(_omfDir, "static", "testFiles", name), "r") as f:
			spec += f.read()
	return {"jasmine": jasmine, "spec": spec}


@app.route("/getComponents/")
@flask_login.login_required
def get_components():
	directory = "data/Component/"
	components = {}
	for dirpath, dirnames, file_names in os.walk(directory):
		for name in file_names:
			if name.endswith(".json"):
				path = os.path.join(dirpath, name)
				with open(path) as f:
					components[name[0:-5]] = json.load(f) # Load the file as a regular object into the dictionary
	return json.dumps(components) # Turn the dictionary of objects into a string


@app.route("/checkConversion/<modelName>/<owner>", methods=["POST","GET"])
@app.route("/checkConversion/<modelName>", methods=["POST","GET"]) # Don't get rid of this route because transEdit.html uses it
def checkConversion(modelName, owner=None):
	"""
	If the path exists, then the conversion is ongoing and the client can't reload their browser yet. If the path does not exist, then either 1) the
	conversion hasn't started yet or 2) the conversion is finished because the ZPID.txt file is gone. If an error file exists, the the conversion
	failed and the client should be notified.
	"""
	print modelName
	if User.cu() == "admin":
		if owner is None:
			owner = User.cu()
	else:
		# owner is not always the current user, sometimes it's "public"
		owner = User.cu()
	# First check for error files
	for filename in ["gridError.txt", "error.txt", "weatherError.txt"]:
		filepath = os.path.join(_omfDir, "data/Model", owner, modelName, filename)
		if os.path.isfile(filepath):
			with open(filepath) as f:
				errorString = f.read()
			return errorString
	# Check for process ID files AFTER checking for error files
	for filename in ["ZPID.txt", "APID.txt", "WPID.txt", "NPID.txt", "CPID.txt"]:
		filepath = os.path.join(_omfDir, "data/Model", owner, modelName, filename)
		if os.path.isfile(filepath):
			return jsonify(exists=True)
	return jsonify(exists=False)		


@app.route("/milsoftImport/<owner>", methods=["POST"])
@flask_login.login_required
def milsoftImport(owner):
	''' API for importing a milsoft feeder. '''
	modelName = request.form.get("modelName","")
	feederName = str(request.form.get("feederNameM","feeder"))
	modelFolder = "data/Model/"+owner+"/"+modelName
	feederNum = request.form.get("feederNum",1)
	# Delete exisitng .std and .seq, .glm files to not clutter model file
	path = "data/Model/"+owner+"/"+modelName
	fileList = safeListdir(path)
	for file in fileList:
		if file.endswith(".glm") or file.endswith(".std") or file.endswith(".seq"):
			os.remove(path+"/"+file)
	stdFile = request.files.get("stdFile")
	seqFile = request.files.get("seqFile")
	stdFile.save(os.path.join(modelFolder,feederName+'.std'))
	seqFile.save(os.path.join(modelFolder,feederName+'.seq'))
	if os.path.isfile("data/Model/"+owner+"/"+modelName+"/gridError.txt"):
		os.remove("data/Model/"+owner+"/"+modelName+"/gridError.txt")
	with open("data/Model/"+owner+"/"+modelName+'/'+feederName+'.std') as stdInput:
		stdString = stdInput.read()
	with open("data/Model/"+owner+"/"+modelName+'/'+feederName+'.seq') as seqInput:
		seqString = seqInput.read()
	importProc = Process(target=milImportBackground, args=[owner, modelName, feederName, feederNum, stdString, seqString])
	importProc.start()
	return 'Success'


def milImportBackground(owner, modelName, feederName, feederNum, stdString, seqString):
	''' Function to run in the background for Milsoft import. '''
	try:
		pid_filepath = os.path.join(_omfDir, "data/Model", owner, modelName, "ZPID.txt")
		with open(pid_filepath, "w") as pid_file:
			pid_file.write(str(os.getpid()))
		modelDir = "data/Model/"+owner+"/"+modelName
		feederDir = modelDir+"/"+feederName+".omd"
		newFeeder = dict(**feeder.newFeederWireframe)
		newFeeder["tree"] = milToGridlab.convert(stdString, seqString)
		with open("./static/schedules.glm","r") as schedFile:
			newFeeder["attachments"] = {"schedules.glm":schedFile.read()}
		try: os.remove(feederDir)
		except: pass
		with open(feederDir, "w") as outFile:
			json.dump(newFeeder, outFile, indent=4)
		with open(feederDir) as feederFile:
			feederTree =  json.load(feederFile)
		if len(feederTree['tree']) < 12:
			with open("data/Model/"+owner+"/"+modelName+"/gridError.txt", "w+") as errorFile:
				errorFile.write('milError')
		os.remove(pid_filepath)
		removeFeeder(owner, modelName, feederNum)
		writeToInput(modelDir, feederName, 'feederName'+str(feederNum))
	except Exception as error:
		with open("data/Model/"+owner+"/"+modelName+"/gridError.txt", "w+") as errorFile:
			errorFile.write("milError")


@app.route("/matpowerImport/<owner>", methods=["POST"])
@flask_login.login_required
def matpowerImport(owner):
	''' API for importing a MATPOWER network. '''
	modelName = request.form.get("modelName","")
	networkName = str(request.form.get("networkNameM","network1"))
	networkNum = request.form.get("networkNum",1)
	# Delete existing .m files to not clutter model.
	path = "data/Model/"+owner+"/"+modelName
	fileList = safeListdir(path)
	for file in fileList:
		if file.endswith(".m"): os.remove(path+"/"+file)
	matFile = request.files["matFile"]
	matFile.save(os.path.join("data/Model/"+owner+"/"+modelName,networkName+'.m'))
	# TODO: Remove error files.
	with open("data/Model/"+owner+"/"+modelName+'/' + "ZPID.txt", "w+") as conFile:
		conFile.write("WORKING")
	importProc = Process(target=matImportBackground, args=[owner, modelName, networkName, networkNum])
	importProc.start()
	return 'Success'


def matImportBackground(owner, modelName, networkName, networkNum):
	''' Function to run in the background for Milsoft import. '''
	try:
		modelDir = "data/Model/"+owner+"/"+modelName
		networkDir = modelDir+"/"+networkName+".m"
		newNet = network.parse(networkDir, filePath=True)
		network.layout(newNet)
		try: os.remove(networkDir)
		except: pass
		with open(networkDir.replace('.m','.omt'), "w") as outFile:
			json.dump(newNet, outFile, indent=4)
		os.remove("data/Model/"+owner+"/"+modelName+'/' + "ZPID.txt")
		removeNetwork(owner, modelName, networkNum)
		writeToInput(modelDir, networkName, 'networkName'+str(networkNum))
	except:
		os.remove("data/Model/"+owner+"/"+modelName+'/' + "ZPID.txt")


@app.route("/gridlabdImport/<owner>", methods=["POST"])
@flask_login.login_required
def gridlabdImport(owner):
	'''This function is used for gridlabdImporting'''
	modelName = request.form.get("modelName","")
	feederName = str(request.form.get("feederNameG",""))
	feederNum = request.form.get("feederNum",1)
	glm = request.files['glmFile']
	# Delete exisitng .std and .seq, .glm files to not clutter model file
	path = "data/Model/"+owner+"/"+modelName
	fileList = safeListdir(path)
	for file in fileList:
		if file.endswith(".glm") or file.endswith(".std") or file.endswith(".seq"):
			os.remove(path+"/"+file)
	# Save .glm file to model folder
	glm.save(os.path.join("data/Model/"+owner+"/"+modelName,feederName+'.glm'))
	with open("data/Model/"+owner+"/"+modelName+'/'+feederName+'.glm') as glmFile:
		glmString = glmFile.read()
	if os.path.isfile("data/Model/"+owner+"/"+modelName+"/gridError.txt"):
		os.remove("data/Model/"+owner+"/"+modelName+"/gridError.txt")
	importProc = Process(target=gridlabImportBackground, args=[owner, modelName, feederName, feederNum, glmString])
	importProc.start()
	return 'Success'


def gridlabImportBackground(owner, modelName, feederName, feederNum, glmString):
	''' Function to run in the background for Milsoft import. '''
	try:
		pid_filepath = os.path.join(_omfDir, "data/Model", owner, modelName, "ZPID.txt")
		with open(pid_filepath, 'w') as pid_file:
			pid_file.write(str(os.getpid()))
		modelDir = "data/Model/"+owner+"/"+modelName
		feederDir = modelDir+"/"+feederName+".omd"
		newFeeder = dict(**feeder.newFeederWireframe)
		newFeeder["tree"] = feeder.parse(glmString, False)
		if not omf.distNetViz.contains_coordinates(newFeeder["tree"]):
			omf.distNetViz.insert_coordinates(newFeeder["tree"])
		with open("./static/schedules.glm","r") as schedFile:
			newFeeder["attachments"] = {"schedules.glm":schedFile.read()}
		try: os.remove(feederDir)
		except: pass
		with open(feederDir, "w") as outFile:
			json.dump(newFeeder, outFile, indent=4)
		os.remove(pid_filepath)
		removeFeeder(owner, modelName, feederNum)
		writeToInput(modelDir, feederName, 'feederName'+str(feederNum))
	except Exception as error:
		with open("data/Model/"+owner+"/"+modelName+"/gridError.txt", "w+") as errorFile:
			errorFile.write('glmError')


@app.route("/scadaLoadshape/<owner>/<feederName>", methods=["POST"])
@flask_login.login_required
def scadaLoadshape(owner,feederName):
	loadName = 'calibration'
	#feederNum = request.form.get("feederNum",1)
	modelName = request.form.get("modelName","")
	modelDir = os.path.join(os.path.dirname(__file__), "data/Model", owner, modelName)
	# delete calibration csv, calibration folder, and error file if they exist
	if os.path.isfile(modelDir + "/error.txt"):
		os.remove(modelDir + "/error.txt")
	if os.path.isfile(modelDir + "/calibration.csv"):
		os.remove(modelDir + "/calibration.csv")
	workDir = modelDir + "/calibration"
	if os.path.isdir(workDir):
		shutil.rmtree(workDir)
		#shutil.rmtree("data/Model/" + owner + "/" +  modelName + "/calibration")
	file = request.files['scadaFile']
	file.save(os.path.join("data/Model/"+owner+"/"+modelName,loadName+".csv"))
	if not os.path.isdir(modelDir+'/calibration/gridlabD'):
		os.makedirs(modelDir+'/calibration/gridlabD')
	feederPath = modelDir+"/"+feederName+".omd"
	scadaPath = modelDir+"/"+loadName+".csv"
	# TODO: parse the csv using .csv library, set simStartDate to earliest timeStamp, length to number of rows, units to difference between first 2
	# timestamps (which is a function in datetime library). We'll need a link to the docs in the import dialog and a short blurb saying how the CSV
	# should be built.
	with open(scadaPath) as csv_file:
		#reader = csv.DictReader(csvFile, delimiter='\t')
		rows = [row for row in csv.DictReader(csv_file)]
		#reader = csv.DictReader(csvFile)
		#rows = [row for row in reader]
	firstDateTime = dt.datetime.strptime(rows[1]["timestamp"], "%m/%d/%Y %H:%M:%S")
	secondDateTime = dt.datetime.strptime(rows[2]["timestamp"], "%m/%d/%Y %H:%M:%S")
	csvLength = len(rows)
	units = (secondDateTime - firstDateTime).total_seconds()
	if abs(units/3600) == 1.0:
		simLengthUnits = 'hours'
	simDate = firstDateTime
	simStartDate = {"Date":simDate,"timeZone":"PST"}
	simLength = csvLength
	# Run omf calibrate in background
	importProc = Process(target=backgroundScadaLoadshape, args =[owner, modelName, workDir, feederPath, scadaPath, simStartDate, simLength, simLengthUnits, "FBS", (0.05,5), 5])
	importProc.start()
	return 'Success'


def backgroundScadaLoadshape(owner, modelName, workDir, feederPath, scadaPath, simStartDate, simLength, simLengthUnits, solver, calibrateError, trim):
	# heavy lifting background process/omfCalibrate and then deletes PID file
	try:
		pid_filepath = os.path.join(_omfDir, "data/Model", owner, modelName, "CPID.txt")
		with open(pid_filepath, 'w') as pid_file:
			pid_file.write(str(os.getpid()))
		omfCalibrate(workDir, feederPath, scadaPath, simStartDate, simLength, simLengthUnits, solver, calibrateError, trim)
		modelDirec="data/Model/" + owner + "/" +  modelName
		# move calibrated file to model folder, old omd files are backedup
		if feederPath.endswith('.omd'):
			os.rename(feederPath, feederPath+".backup")
		os.rename(workDir+'/calibratedFeeder.omd',feederPath)
		# shutil.move(workDir+"/"+feederFileName, modelDirec)
		os.remove(pid_filepath)
	except Exception as error:
		modelDirec="data/Model/" + owner + "/" +  modelName
		errorString = ''.join(error)
		with open(modelDirec+'/error.txt',"w+") as errorFile:
		 	errorFile.write("The CSV used is incorrectly formatted. Please refer to the OMF Wiki for CSV formatting information. The Wiki can be access by clicking the Help button on the toolbar.")


@app.route("/loadModelingAmi/<owner>/<feederName>", methods=["POST"])
def loadModelingAmi(owner,feederName):
	loadName = 'ami'
	feederNum = request.form.get("feederNum",1)
	modelName = request.form.get("modelName","")
	if os.path.isfile("data/Model/" + owner + "/" +  modelName + "/amiError.txt"):
		os.remove("data/Model/" + owner + "/" +  modelName + "/amiError.txt")
	if os.path.isfile("data/Model/" + owner + "/" +  modelName + "/amiLoad.csv"):
		os.remove("data/Model/" + owner + "/" +  modelName + "/amiLoad.csv")
	file = request.files['amiFile']
	file.save(os.path.join("data/Model/"+owner+"/"+modelName,loadName+".csv"))
	modelDir = "data/Model/"+owner+"/"+modelName
	omdPath = modelDir+"/"+feederName+".omd"
	amiPath = modelDir+"/"+loadName+".csv"
	importProc = Process(target=backgroundLoadModelingAmi, args =[owner, modelName, modelDir, omdPath, amiPath])
	importProc.start()
	return 'Success'


def backgroundLoadModelingAmi(owner, modelName, workDir, omdPath, amiPath):
	try:
		pid_filepath = os.path.join(_omfDir, "data/Model", owner, modelName, "APID.txt")
		with open(pid_filepath, 'w') as pid_file:
			pid_file.write(str(os.getpid()))
		outDir = workDir + '/amiOutput/'
		writeNewGlmAndPlayers(omdPath, amiPath, outDir)
		modelDirec="data/Model/" + owner + "/" +  modelName
		os.remove(pid_filepath)
	except Exception as error:
		with open("data/Model/"+owner+"/"+modelName+"/error.txt", "w+") as errorFile:
			errorFile.write("amiError")


# TODO: Check if rename mdb files worked
@app.route("/cymeImport/<owner>", methods=["POST"])
@flask_login.login_required
def cymeImport(owner):
	''' API for importing a cyme feeder. '''
	modelName = request.form.get("modelName","")
	feederName = str(request.form.get("feederNameC",""))
	feederNum = request.form.get("feederNum",1)
	modelFolder = "data/Model/"+owner+"/"+modelName
	mdbFileObject = request.files["mdbNetFile"]
	# Saves .mdb files to model folder
	mdbFileObject.save(os.path.join(modelFolder,mdbFileObject.filename))
	if os.path.isfile("data/Model/"+owner+"/"+modelName+"/gridError.txt"):
		os.remove("data/Model/"+owner+"/"+modelName+"/gridError.txt")
	importProc = Process(target=cymeImportBackground, args=[owner, modelName, feederName, feederNum, mdbFileObject.filename])
	importProc.start()
	return 'Success'


def cymeImportBackground(owner, modelName, feederName, feederNum, mdbFileName):
	''' Function to run in the background for Milsoft import. '''
	try:
		pid_filepath = os.path.join(_omfDir, "data/Model", owner, modelName, "ZPID.txt")
		with open(pid_filepath, 'w') as pid_file:
			pid_file.write(str(os.getpid()))
		modelDir = "data/Model/"+owner+"/"+modelName+"/"
		feederDir = modelDir+"/"+feederName+".omd"
		newFeeder = dict(**feeder.newFeederWireframe)
		print mdbFileName
		newFeeder["tree"] = cymeToGridlab.convertCymeModel(modelDir + mdbFileName, modelDir)
		with open("./static/schedules.glm","r") as schedFile:
			newFeeder["attachments"] = {"schedules.glm":schedFile.read()}
		try: os.remove(feederDir)
		except: pass
		with open(feederDir, "w") as outFile:
			json.dump(newFeeder, outFile, indent=4)
		os.remove(pid_filepath)
		removeFeeder(owner, modelName, feederNum)
		writeToInput(modelDir, feederName, 'feederName'+str(feederNum))
	except Exception as error:
		with open("data/Model/"+owner+"/"+modelName+"/gridError.txt", "w+") as errorFile:
			errorFile.write('cymeError')


@app.route("/newSimpleFeeder/<owner>/<modelName>/<feederNum>/<writeInput>", methods=["POST", "GET"])
def newSimpleFeeder(owner, modelName, feederNum=1, writeInput=False, feederName='feeder1'):
	if User.cu() == "admin" or owner == User.cu():
		modelDir = os.path.join(_omfDir, "data", "Model", owner, modelName)
		for i in range(2,6):
			if not os.path.isfile(os.path.join(modelDir,feederName+'.omd')):
				with open("./static/SimpleFeeder.json", "r") as simpleFeederFile:
					with open(os.path.join(modelDir, feederName+".omd"), "w") as outFile:
						outFile.write(simpleFeederFile.read())
				break
			else:
				feederName = 'feeder'+str(i)
		if writeInput:
			writeToInput(modelDir, feederName, 'feederName'+str(feederNum))
		return 'Success'
	else:
		return 'Invalid Login'


@app.route("/newSimpleNetwork/<owner>/<modelName>/<networkNum>/<writeInput>", methods=["POST", "GET"])
def newSimpleNetwork(owner, modelName, networkNum=1, writeInput=False, networkName='network1'):
	if User.cu() == "admin" or owner == User.cu():
		modelDir = os.path.join(_omfDir, "data", "Model", owner, modelName)
		for i in range(2,6):
			if not os.path.isfile(os.path.join(modelDir,networkName+'.omt')):
				with open("./static/SimpleNetwork.json", "r") as simpleNetworkFile:
					with open(os.path.join(modelDir, networkName+".omt"), "w") as outFile:
						outFile.write(simpleNetworkFile.read())
				break
			else: networkName = 'network'+str(i)
		if writeInput: writeToInput(modelDir, networkName, 'networkName'+str(networkNum))
		return 'Success'
	else:
		return 'Invalid Login'


@app.route("/newBlankFeeder/<owner>", methods=["POST"])
@flask_login.login_required
def newBlankFeeder(owner):
	'''This function is used for creating a new blank feeder.'''
	modelName = request.form.get("modelName","")
	feederName = str(request.form.get("feederNameNew"))
	feederNum = request.form.get("feederNum",1)
	if feederName == '': feederName = 'feeder'
	modelDir = os.path.join(_omfDir, "data","Model", owner, modelName)
	try:
		os.remove("data/Model/"+owner+"/"+modelName+'/' + "ZPID.txt")
		print "removed, ", ("data/Model/"+owner+"/"+modelName+'/' + "ZPID.txt")
	except: pass
	removeFeeder(owner, modelName, feederNum)
	newSimpleFeeder(owner, modelName, feederNum, False, feederName)
	writeToInput(modelDir, feederName, 'feederName'+str(feederNum))
	if request.form.get("referrer") == "distribution":
		return redirect(url_for("distribution_get", owner=owner, model_name=modelName, feeder_num=feederNum))
	return redirect(url_for('feederGet', owner=owner, modelName=modelName, feederNum=feederNum))


@app.route("/newBlankNetwork/<owner>", methods=["POST"])
@flask_login.login_required
def newBlankNetwork(owner):
	'''This function is used for creating a new blank network.'''
	modelName = request.form.get("modelName","")
	networkName = str(request.form.get("networkNameNew"))
	networkNum = request.form.get("networkNum",1)
	if networkName == '': networkName = 'network1'
	modelDir = os.path.join(_omfDir, "data","Model", owner, modelName)
	try:
		os.remove("data/Model/"+owner+"/"+modelName+'/' + "ZPID.txt")
		print "removed, ", ("data/Model/"+owner+"/"+modelName+'/' + "ZPID.txt")
	except: pass
	removeNetwork(owner, modelName, networkNum)
	newSimpleNetwork(owner, modelName, networkNum, False, networkName)
	writeToInput(modelDir, networkName, 'networkName'+str(networkNum))
	return redirect(url_for('networkGet', owner=owner, modelName=modelName, networkNum=networkNum))


@app.route("/feederData/<owner>/<modelName>/<feederName>/")
@app.route("/feederData/<owner>/<modelName>/<feederName>/<modelFeeder>")
@flask_login.login_required
def feederData(owner, modelName, feederName, modelFeeder=False):
	#MAYBEFIX: fix modelFeeder capability.
	if User.cu()=="admin" or owner==User.cu() or owner=="public":
		with open("data/Model/" + owner + "/" + modelName + "/" + feederName + ".omd", "r") as feedFile:
			return feedFile.read()


@app.route("/networkData/<owner>/<modelName>/<networkName>/")
@flask_login.login_required
def networkData(owner, modelName, networkName):
	if User.cu()=="admin" or owner==User.cu() or owner=="public":
		with open("data/Model/" + owner + "/" + modelName + "/" + networkName + ".omt", "r") as netFile:
			thisNet = json.load(netFile)
		return json.dumps(thisNet)
		# return jsonify(netFile.read())


@app.route("/saveFeeder/<owner>/<modelName>/<feederName>/<int:feederNum>", methods=["POST"])
@flask_login.login_required
def saveFeeder(owner, modelName, feederName, feederNum):
	"""Save feeder data. Also used for cancelling a file import, file conversion, or feeder-load overwrite."""
	print "Saving feeder for:%s, with model: %s, and feeder: %s"%(owner, modelName, feederName)
	if owner == User.cu() or "admin" == User.cu() or owner == "public":
		model_dir = os.path.join(_omfDir, "data/Model", owner, modelName)
		for filename in ["gridError.txt", "error.txt", "weatherError.txt"]:
			error_file = os.path.join(model_dir, filename)
			if os.path.isfile(error_file):
				try:
					os.remove(error_file)
				except OSError as e:
					if e.errno ==2:
						# Tried to remove a nonexistant file
						pass
		# Do NOT cancel any PPID.txt or PID.txt processes.
		for filename in ["ZPID.txt", "APID.txt", "NPID.txt", "CPID.txt", "WPID.txt"]:
			pid_filepath = os.path.join(model_dir, filename)
			if os.path.isfile(pid_filepath):
				try:
					with open(pid_filepath) as f:
						fcntl.flock(f, fcntl.LOCK_SH) # Get a shared lock
						pid = f.read()
						fcntl.flock(f, fcntl.LOCK_UN) # Release the shared lock
					os.remove(pid_filepath)
					os.kill(int(pid), signal.SIGTERM)
				except IOError as e:
					if e.errno == 2:
						# Tried to open a nonexistent file. Presumably, some other process opened the used the pid file and deleted it before this process
						# could use it
						pass
					else:
						raise
				except OSError as e:
					if e.errno == 2:
						# Tried to remove a nonexistent file
						pass
					elif e.errno == 3:
						# Tried to kill a process with a pid that doesn't map to an existing process.
						pass
					else:
						raise
		writeToInput(model_dir, feederName, 'feederName' + str(feederNum))
		payload = json.loads(request.form.to_dict().get("feederObjectJson","{}"))
		feeder_file = os.path.join(model_dir, feederName + ".omd")
		if os.path.isfile(feeder_file):
			with open(feeder_file, "r+") as outFile:
				fcntl.flock(outFile, fcntl.LOCK_EX) # Get an exclusive lock
				outFile.truncate()
				json.dump(payload, outFile, indent=4) # This route is slow only because this line takes forever. We want the indentation so we keep this line
				fcntl.flock(outFile, fcntl.LOCK_UN) # Release the exclusive lock
		else:
			# The feeder_file should always exist, but just in case there was an error, we allow the recreation of the file
			with open(feeder_file, "w") as outFile:
				fcntl.flock(outFile, fcntl.LOCK_EX) # Get an exclusive lock
				json.dump(payload, outFile, indent=4) # This route is slow only because this line takes forever. We want the indentation so we keep this line
				fcntl.flock(outFile, fcntl.LOCK_UN) # Release the exclusive lock
	return 'Success'


@app.route("/saveNetwork/<owner>/<modelName>/<networkName>", methods=["POST"])
@flask_login.login_required
def saveNetwork(owner, modelName, networkName):
	''' Save network data. '''
	print "Saving network for:%s, with model: %s, and network: %s"%(owner, modelName, networkName)
	if owner == User.cu() or "admin" == User.cu() or owner=="public":
		with open("data/Model/" + owner + "/" + modelName + "/" + networkName + ".omt", "w") as outFile:
			payload = json.loads(request.form.to_dict().get("networkObjectJson","{}"))
			json.dump(payload, outFile, indent=4)
	return 'Success'


@app.route("/renameFeeder/<owner>/<modelName>/<oldName>/<newName>/<feederNum>", methods=["GET", "POST"])
@flask_login.login_required
def renameFeeder(owner, modelName, oldName, newName, feederNum):
	''' rename a feeder. '''
	model_dir_path = os.path.join(_omfDir, "data/Model", owner, modelName)
	new_feeder_filepath = os.path.join(model_dir_path, newName + ".omd")
	old_feeder_filepath = os.path.join(model_dir_path, oldName + ".omd")
	if os.path.isfile(new_feeder_filepath) or not os.path.isfile(old_feeder_filepath):
		return "Failure"
	with open(old_feeder_filepath) as f:
		fcntl.flock(f, fcntl.LOCK_EX)
		os.rename(old_feeder_filepath, new_feeder_filepath)
		fcntl.flock(f, fcntl.LOCK_UN)
	writeToInput(model_dir_path, newName, 'feederName' + str(feederNum))
	return 'Success'


@app.route("/renameNetwork/<owner>/<modelName>/<oldName>/<networkName>/<networkNum>", methods=["POST"])
@flask_login.login_required
def renameNetwork(owner, modelName, oldName, networkName, networkNum):
	''' rename a feeder. '''
	modelDir = os.path.join(_omfDir, "data","Model", owner, modelName)
	networkDir = os.path.join(modelDir, networkName+'.omt')
	oldnetworkDir = os.path.join(modelDir, oldName+'.omt')
	if not os.path.isfile(networkDir) and os.path.isfile(oldnetworkDir):
		with open(oldnetworkDir, "r") as networkIn:
			with open(networkDir, "w") as outFile:
				outFile.write(networkIn.read())
	elif os.path.isfile(networkDir):
		return 'Failure'
	elif not os.path.isfile(oldnetworkDir):
		return 'Failure'
	os.remove(oldnetworkDir)
	writeToInput(modelDir, networkName, 'networkName'+str(networkNum))
	return 'Success'


@app.route("/removeFeeder/<owner>/<modelName>/<feederNum>", methods=["GET", "POST"])
@app.route("/removeFeeder/<owner>/<modelName>/<feederNum>/<feederName>", methods=["GET", "POST"])
@flask_login.login_required
def removeFeeder(owner, modelName, feederNum, feederName=None):
	'''Remove a feeder from input data.'''
	if User.cu() == "admin" or owner == User.cu():
		try:
			modelDir = os.path.join(_omfDir, "data","Model", owner, modelName)
			with open(modelDir + "/allInputData.json") as inJson:
				allInput = json.load(inJson)
			try:
				feederName = str(allInput.get('feederName'+str(feederNum)))
				os.remove(os.path.join(modelDir, feederName +'.omd'))
			except: print "Couldn't remove feeder file in web.removeFeeder()."
			allInput.pop("feederName"+str(feederNum))
			with open(modelDir+"/allInputData.json","w") as inputFile:
				json.dump(allInput, inputFile, indent=4)
			return 'Success'
		except:
			return 'Failed'
	else:
		return 'Invalid Login'


@app.route("/loadFeeder/<frfeederName>/<frmodelName>/<modelName>/<feederNum>/<frUser>/<owner>", methods=["GET", "POST"])
@flask_login.login_required
def loadFeeder(frfeederName, frmodelName, modelName, feederNum, frUser, owner):
	'''Load a feeder from one model to another.'''
	if frUser != "public":
		frUser = User.cu()
		frmodelDir = "./data/Model/" + frUser + "/" + frmodelName
	elif frUser == "public":
		frmodelDir = "./static/publicFeeders"
	#print "Entered loadFeeder with info: frfeederName %s, frmodelName: %s, modelName: %s, feederNum: %s"%(frfeederName, frmodelName, str(modelName), str(feederNum))
	modelDir = "./data/Model/" + owner + "/" + modelName
	with open(modelDir + "/allInputData.json") as inJson:
		fcntl.flock(inJson, fcntl.LOCK_SH) # Get a shared lock
		feederName = json.load(inJson).get('feederName' + str(feederNum))
		fcntl.flock(inJson, fcntl.LOCK_UN) # Release the shared lock
	with open(os.path.join(frmodelDir, frfeederName+'.omd'), "r") as inFeeder:
		with open(os.path.join(modelDir, feederName+".omd"), "w") as outFile:
			fcntl.flock(inFeeder, fcntl.LOCK_SH) # Get a shared lock
			fcntl.flock(outFile, fcntl.LOCK_EX) # Get an exclusive lock
			outFile.write(inFeeder.read())
			fcntl.flock(outFile, fcntl.LOCK_UN) # Release the exclusive lock
			fcntl.flock(inFeeder, fcntl.LOCK_UN) # Release the shared lock
	if request.form.get("referrer") == "distribution":
		return redirect(url_for("distribution_get", owner=owner, model_name=modelName, feeder_num=feederNum))
	return redirect(url_for('feederGet', owner=owner, modelName=modelName, feederNum=feederNum))


@app.route("/cleanUpFeeders/<owner>/<modelName>", methods=["GET", "POST"])
@flask_login.login_required
def cleanUpFeeders(owner, modelName):
	'''Go through allInputData and fix feeder Name keys'''
	modelDir = "./data/Model/" + owner + "/" + modelName
	with open(modelDir + "/allInputData.json") as inJson:
		allInput = json.load(inJson)
	feeders = {}
	feederKeys = ['feederName1', 'feederName2', 'feederName3', 'feederName4', 'feederName5']
	import pprint as pprint
	pprint.pprint(allInput)
	for key in feederKeys:
		feederName = allInput.get(key,'')
		if feederName != '':
			feeders[key] = feederName
		allInput.pop(key,None)
	for i,key in enumerate(sorted(feeders)):
		allInput['feederName'+str(i+1)] = feeders[key]
	pprint.pprint(allInput)
	with open(modelDir+"/allInputData.json","w") as inputFile:
		json.dump(allInput, inputFile, indent = 4)
	return redirect("/model/" + owner + "/" + modelName)


@app.route("/removeNetwork/<owner>/<modelName>/<networkNum>", methods=["GET","POST"])
@app.route("/removeNetwork/<owner>/<modelName>/<networkNum>/<networkName>", methods=["GET","POST"])
@flask_login.login_required
def removeNetwork(owner, modelName, networkNum, networkName=None):
	'''Remove a network from input data.'''
	if User.cu() == "admin" or owner == User.cu():
		try:
			modelDir = os.path.join(_omfDir, "data","Model", owner, modelName)
			with open(modelDir + "/allInputData.json") as inJson:
				allInput = json.load(inJson)
			try:
				networkName = str(allInput.get('networkName'+str(networkNum)))
				os.remove(os.path.join(modelDir, networkName +'.omt'))
			except: print "Couldn't remove network file in web.removeNetwork()."
			allInput.pop("networkName"+str(networkNum))
			with open(modelDir+"/allInputData.json","w") as inputFile:
				json.dump(allInput, inputFile, indent = 4)
			return 'Success'
		except:
			return 'Failed'
	else:
		return 'Invalid Login'


@app.route("/climateChange/<owner>/<feederName>", methods=["POST"])
@flask_login.login_required
def climateChange(owner, feederName):
	model_name = request.form.get('modelName')
	model_dir = 'data/Model/' + owner + '/' + model_name
	omdPath = model_dir + '/' + feederName + '.omd'
	# Remove files that could be left over from a previous run
	filepaths = [
		os.path.join(model_dir, "error.txt"),
		os.path.join(model_dir, "weatherAirport.csv"), # Old deleted historical weather option
		os.path.join(model_dir, "uscrn-weather-data.csv")
	]
	for fp in filepaths:
		if os.path.isfile(fp):
			os.remove(fp)
	# Don't bother writing WPID.txt here because /checkConversion doesn't distinguish between non-started processes and non-existant processes
	importProc = Process(target=backgroundClimateChange, args=[omdPath, owner, model_name])
	importProc.start()
	return "Success"


def backgroundClimateChange(omdPath, owner, modelName):
	try:
		pid_filepath = os.path.join(_omfDir, "data/Model", owner, modelName, "WPID.txt")
		with open(pid_filepath, 'w') as pid_file:
			pid_file.write(str(os.getpid()))
		importOption = request.form.get('climateImportOption')
		if importOption is None:
			raise Exception("Invalid weather import option selected.")
		if importOption == "USCRNImport":
			try:
				year = int(request.form.get("uscrnYear"))
			except:
				raise Exception("Invalid year was submitted.")
			station = request.form.get("uscrnStation")
			if station is None or len(station) == 0:
				raise Exception("Invalid station was submitted.")
			weather.attachHistoricalWeather(omdPath, year, station)
		elif importOption == 'tmyImport':
			# Old calibration logic. Preserve for the sake of the 'tmyImport' option
			with open(omdPath, 'r') as inFile:
				feederJson = json.load(inFile)
				for key in feederJson['tree'].keys():
					if (feederJson['tree'][key].get('object') == 'climate') or (feederJson['tree'][key].get('name') == 'weatherReader'):
						del feederJson['tree'][key]
				for key in feederJson['attachments'].keys():
					if (key.endswith('.tmy2')) or (key == 'weatherAirport.csv'):
						del feederJson['attachments'][key]
			# Old tmy2 weather operation
			zipCode = request.form.get('zipCode')
			climateName = weather.zipCodeToClimateName(zipCode)
			tmyFilePath = 'data/Climate/' + climateName + '.tmy2'
			feederJson['tree'][feeder.getMaxKey(feederJson['tree'])+1] = {'object':'climate','name':'Climate','interpolate':'QUADRATIC', 'tmyfile':'climate.tmy2'}
			with open(tmyFilePath) as tmyFile:
				feederJson['attachments']['climate.tmy2'] = tmyFile.read()
			with open(omdPath, 'w') as outFile:
				fcntl.flock(outFile, fcntl.LOCK_EX)
				json.dump(feederJson, outFile, indent=4)
				fcntl.flock(outFile, fcntl.LOCK_UN)
		try:
			os.remove(pid_filepath)
		except:
			pass
	except Exception as e:
		with open("data/Model/"+owner+"/"+modelName+"/error.txt", "w") as errorFile:
			message = "climateError" if (e.message is None or e.message is "") else e.message
			errorFile.write(message)


@app.route("/anonymize/<owner>/<feederName>", methods=["POST"])
@flask_login.login_required
def anonymize(owner, feederName):
	modelName = request.form.get('modelName')
	modelDir = 'data/Model/' + owner + '/' + modelName
	omdPath = modelDir + '/' + feederName + '.omd'
	importProc = Process(target=backgroundAnonymize, args=[modelDir, omdPath, owner, modelName])
	importProc.start()
	return 'Success'


def backgroundAnonymize(modelDir, omdPath, owner, modelName):
	try:
		pid_filepath = os.path.join(_omfDir, "data/Model", owner, modelName, "NPID.txt")
		with open(pid_filepath, 'w') as pid_file:
			pid_file.write(str(os.getpid()))
		with open(omdPath, 'r') as inFile:
			inFeeder = json.load(inFile)
			# Name Option
			nameOption = request.form.get('anonymizeNameOption')
			newNameKey = None
			if nameOption == 'pseudonymize':
				newNameKey = anonymization.distPseudomizeNames(inFeeder)
			elif nameOption == 'randomize':
				anonymization.distRandomizeNames(inFeeder)
			# Location Option
			locOption = request.form.get('anonymizeLocationOption')
			if locOption == 'translation':
				translationRight = request.form.get('translateRight')
				translationUp = request.form.get('translateUp')
				rotation = request.form.get('rotate')
				anonymization.distTranslateLocations(inFeeder, translationRight, translationUp, rotation)
			elif locOption == 'randomize':
				anonymization.distRandomizeLocations(inFeeder)
			elif locOption == 'forceLayout':
				omf.distNetViz.insert_coordinates(inFeeder["tree"])
			# Electrical Properties
			if request.form.get('modifyLengthSize'):
				anonymization.distModifyTriplexLengths(inFeeder)
				anonymization.distModifyConductorLengths(inFeeder)
			if request.form.get('smoothLoadGen'):
				anonymization.distSmoothLoads(inFeeder)
			if request.form.get('shuffleLoadGen'):
				shufPerc = request.form.get('shufflePerc')
				anonymization.distShuffleLoads(inFeeder, shufPerc)
			if request.form.get('addNoise'):
				noisePerc = request.form.get('noisePerc')
				anonymization.distAddNoise(inFeeder, noisePerc)
		with open(omdPath, 'w') as outFile:
			fcntl.flock(outFile, fcntl.LOCK_EX)
			json.dump(inFeeder, outFile, indent=4)
			fcntl.flock(outFile, fcntl.LOCK_UN)
		os.remove(pid_filepath)
		if newNameKey:
			return newNameKey
	except Exception as error:
		with open("data/Model/"+owner+"/"+modelName+"/gridError.txt", "w+") as errorFile:
			errorFile.write('anonymizeError')


@app.route("/zillowHouses", methods=["POST"])
@flask_login.login_required
def zillow_houses():
	owner = request.form.get("owner")
	model_name = request.form.get("modelName")
	model_dir = os.path.join(_omfDir, "data/Model", owner, model_name)
	error_filepath = os.path.join(model_dir, "error.txt")
	if os.path.isfile(error_filepath):
		os.remove(error_filepath)
	payload_filepath = os.path.join(model_dir, "zillow_houses.json")
	if os.path.isfile(payload_filepath):
		os.remove(payload_filepath)
	# Write the ZPID.txt file now so there is no way the client will get a 404 when they check for an ongoing process. Process hasn't started yet though.
	zpid_filepath = os.path.join(model_dir, "ZPID.txt")
	with open(zpid_filepath, 'w') as f:
		f.write("")
	importProc = Process(target=background_zillow_houses, args=[model_dir])
	importProc.start()
	return ""


def background_zillow_houses(model_dir):
	try:
		pid_filepath = os.path.join(model_dir, "ZPID.txt")
		with open(pid_filepath, 'w') as pid_file:
			pid_file.write(str(os.getpid()))
		triplex_objects = json.loads(request.form.get("triplexObjects"))
		#triplex_objects = request.form.get("triplexObjects") # error test
		zillow_houses = {}
		for obj in triplex_objects:
			try:
				# Try to get real house data
				house = omf.loadModeling.zillowHouse(obj["latitude"], obj["longitude"])
				zillow_houses[obj["key"]] = house
			except:
				# If a request for some house fails, get a random house
				house = omf.loadModeling.zillowHouse(0, 0, pureRandom=True)
				zillow_houses[obj["key"]] = house
			# The APIs we use require us to limit our requests to a maximum of 1 per second. Exceeding that throughput will get us IP banned faster.
			time.sleep(1)
		payload_filepath = os.path.join(model_dir, "zillow_houses.json")
		with open(payload_filepath, 'w') as f:
			json.dump(zillow_houses, f)
		os.remove(pid_filepath)
	except Exception as e:
		with open(os.path.join(model_dir, "error.txt"), 'w') as error_file:
			message = "zillow_error" if e.message is None else e.message
			error_file.write(message)


@app.route("/checkZillowHouses", methods=["POST"])
@flask_login.login_required
def check_zillow_houses():
	owner = request.form.get("owner")
	model_name = request.form.get("modelName")
	model_dir = os.path.join(_omfDir, "data/Model", owner, model_name)
	if owner == User.cu() or "admin" == User.cu():
		error_filepath = os.path.join(model_dir, "error.txt")
		if os.path.isfile(error_filepath):
			with open(error_filepath) as f:
				error_message = f.read()
			return (error_message, 500)
		pid_filepath = os.path.join(model_dir, "ZPID.txt")
		if os.path.isfile(pid_filepath):
			return ("", 202)
		payload_filepath = os.path.join(model_dir, "zillow_houses.json")
		if os.path.isfile(payload_filepath):
			with open(payload_filepath) as f:
				data = json.load(f)
			return jsonify(data)
	abort(404)


@app.route("/anonymizeTran/<owner>/<networkName>", methods=["POST"])
@flask_login.login_required
def anonymizeTran(owner, networkName):
	modelName = request.form.get('modelName')
	modelDir = 'data/Model/' + owner + '/' + modelName
	omtPath = modelDir + '/' + networkName + '.omt'
	importProc = Process(target=backgroundAnonymizeTran, args =[modelDir, omtPath])
	importProc.start()
	pid = str(importProc.pid)
	with open(modelDir + '/TPPID.txt', 'w+') as outFile:
		outFile.write(pid)
	return 'Success'


def backgroundAnonymizeTran(modelDir, omtPath):
	with open(omtPath, 'r') as inFile:
		inNetwork = json.load(inFile)
		# Name Options
		nameOption = request.form.get('anonymizeNameOption')
		if nameOption == 'pseudonymize':
			newBusKey = anonymization.tranPseudomizeNames(inNetwork)
		elif nameOption == 'randomize':
			anonymization.tranRandomizeNames(inNetwork)
		# Location Options
		locOption = request.form.get('anonymizeLocationOption')
		if locOption == 'translation':
			translationRight = request.form.get('translateRight')
			translationUp = request.form.get('translateUp')
			rotation = request.form.get('rotate')
			anonymization.tranTranslateLocations(inNetwork, translationRight, translationUp, rotation)
		elif locOption == 'randomize':
			anonymization.tranRandomizeLocations(inNetwork)
		# Electrical Properties
		if request.form.get('shuffleLoadGen'):
			shufPerc = request.form.get('shufflePerc')
			anonymization.tranShuffleLoadsAndGens(inNetwork, shufPerc)
		if request.form.get('addNoise'):
			noisePerc = request.form.get('noisePerc')
			anonymization.tranAddNoise(inNetwork, noisePerc)
	with open(omtPath, 'w') as outFile:
		json.dump(inNetwork, outFile, indent=4)
	os.remove(modelDir + '/TPPID.txt')
	if newBusKey:
		return newBusKey


@app.route("/checkAnonymizeTran/<owner>/<modelName>", methods=["POST","GET"])
def checkAnonymizeTran(owner, modelName):
	pidPath = ('data/Model/' + owner + '/' + modelName + '/TPPID.txt')
	# print 'Check conversion status:', os.path.exists(pidPath), 'for path', pidPath
	# checks to see if PID file exists, if theres no PID file process is done.
	return jsonify(exists=os.path.exists(pidPath))


@app.route('/displayMap/<owner>/<modelName>/<feederNum>', methods=["GET"])
def displayOmdMap(owner, modelName, feederNum):
	'''Function to render omd on a leaflet map using a new template '''
	modelDir = os.path.join(_omfDir, "data","Model", owner, modelName)
	with open(os.path.join(modelDir, "allInputData.json"), "r") as jsonFile:
		feederDict = json.load(jsonFile)
		feederName = feederDict.get('feederName' + str(feederNum))
	feederFile = os.path.join(modelDir, feederName + ".omd")
	geojson = omf.geo.omdGeoJson(feederFile)
	return render_template('geoJsonMap.html', geojson=geojson)


@app.route('/commsMap/<owner>/<modelName>/<feederNum>', methods=["GET"])
def commsMap(owner, modelName, feederNum):
	'''Function to render omc on a leaflet map using a new template '''
	modelDir = os.path.join(_omfDir, "data","Model", owner, modelName)
	with open(os.path.join(modelDir, "allInputData.json"), "r") as jsonFile:
		feederDict = json.load(jsonFile)
		feederName = feederDict.get('feederName' + str(feederNum))
	feederFile = os.path.join(modelDir, feederName + ".omc")
	with open(feederFile) as commsGeoJson:
		geojson = json.load(commsGeoJson)
	return render_template('commsNetViz.html', geojson=geojson, owner=owner, modelName=modelName, feederNum=feederNum, feederName=feederName)

@app.route('/redisplayGrid', methods=["POST"])
def redisplayGrid():
	'''Redisplay comms grid on edits'''
	geoDict = request.get_json()
	nxG = omf.comms.omcToNxg(geoDict)
	omf.comms.clearFiber(nxG)
	omf.comms.clearRFEdges(nxG)
	omf.comms.setFiber(nxG)
	omf.comms.setRF(nxG)
	omf.comms.setFiberCapacity(nxG)
	omf.comms.setRFEdgeCapacity(nxG)
	omf.comms.calcBandwidth(nxG)
	#need to runs comms updates here
	geoJson = omf.comms.graphGeoJson(nxG)
	return jsonify(newgeojson=geoJson)

@app.route('/saveCommsMap/<owner>/<modelName>/<feederName>/<feederNum>', methods=["POST"])
def saveCommsMap(owner, modelName, feederName, feederNum):
	try:
		geoDict = request.get_json()
		model_dir = os.path.join(_omfDir, "data/Model", owner, modelName)
		omf.comms.saveOmc(geoDict, model_dir, feederName)
		return jsonify(savemessage='Communications network saved')
	except:
		return jsonify(savemessage='Error saving communications network')

###################################################
# OTHER FUNCTIONS
###################################################

@app.route("/")
@flask_login.login_required
def root():
	''' Render the home screen of the OMF. '''
	# Gather object names.
	publicModels = [{"owner":"public","name":x} for x in safeListdir("data/Model/public/")]
	userModels = [{"owner":User.cu(), "name":x} for x in safeListdir("data/Model/" + User.cu())]
	allModels = publicModels + userModels
	# Allow admin to see all model instances.
	isAdmin = User.cu() == "admin"
	if isAdmin:
		allModels = [{"owner":owner,"name":mod} for owner in safeListdir("data/Model/")
			for mod in safeListdir("data/Model/" + owner)]
	# Grab metadata for model instances.
	for mod in allModels:
		try:
			modPath = "data/Model/" + mod["owner"] + "/" + mod["name"]
			allInput = json.load(open(modPath + "/allInputData.json"))
			mod["runTime"] = allInput.get("runTime","")
			mod["modelType"] = allInput.get("modelType","")
			try:
				mod["status"] = getattr(models, mod["modelType"]).getStatus(modPath)
				creation = allInput.get("created","")
				mod["created"] = creation[0:creation.rfind('.')]
				# mod["editDate"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(os.stat(modPath).st_ctime))
			except: # the model type was deprecated, so the getattr will fail.
				mod["status"] = "stopped"
				mod["editDate"] = "N/A"
		except:
			continue
	allModels.sort(key=lambda x:x.get('created',''), reverse=True)
	# Get tooltips for model types.
	modelTips = {}
	for name in models.__all__:
		try:
			modelTips[name] = getattr(omf.models,name).tooltip
		except:
			pass
	# Generate list of model types.
	modelNames = []
	for modelName in models.__all__:
		thisModel = getattr(models, modelName)
		hideFlag = thisModel.__dict__.get('hidden', False)
		#HACK: support for old underscore hiding.
		hideChar = modelName.startswith('_')
		if not(hideFlag or hideChar):
			modelNames.append(modelName)
	modelNames.sort()
	return render_template("home.html", models=allModels, current_user=User.cu(), is_admin=isAdmin, modelNames=modelNames, modelTips=modelTips)


@app.route("/delete/<objectType>/<owner>/<objectName>", methods=["POST"])
@flask_login.login_required
def delete(objectType, objectName, owner):
	''' Delete models or feeders. '''
	if owner != User.cu() and User.cu() != "admin":
		return False
	if objectType == "Feeder":
		os.remove("data/Model/" + owner + "/" + objectName + "/" + "feeder.omd")
		return redirect("/#feeders")
	elif objectType == "Model":
		shutil.rmtree("data/Model/" + owner + "/" + objectName)
	return redirect("/")


@app.route("/downloadModelData/<owner>/<modelName>/<path:fullPath>")
@flask_login.login_required
def downloadModelData(owner, modelName, fullPath):
	pathPieces = fullPath.split('/')
	return send_from_directory("data/Model/"+owner+"/"+modelName+"/"+"/".join(pathPieces[0:-1]), pathPieces[-1])


@app.route("/uniqObjName/<objtype>/<owner>/<name>")
@app.route("/uniqObjName/<objtype>/<owner>/<name>/<modelName>")
@flask_login.login_required
def uniqObjName(objtype, owner, name, modelName=False):
	""" Checks if a given object type/owner/name is unique. More like checks if a file exists on the server.
	"""
	print "Entered uniqobjname", owner, name, modelName
	if objtype == "Model":
		path = "data/Model/" + owner + "/" + name
	elif objtype == "Feeder":
		if name == 'feeder':
			return jsonify(exists=True)
		if owner != "public":
			path = "./data/Model/" + owner + "/" + modelName + "/" + name + ".omd"
		else:
			path = "static/publicFeeders/" + name + ".omd"
	elif objtype == "Network":
		path = "data/Model/" + owner + "/" + modelName + "/" + name + ".omt"
		if name == 'feeder':
			return jsonify(exists=True)
	return jsonify(exists=os.path.exists(path))


if __name__ == "__main__":
	if platform.system() == "Darwin":  # MacOS
		os.environ['no_proxy'] = '*' # Workaround for macOS fork behavior with multiprocessing and urllib.
	template_files = ["templates/"+ x  for x in safeListdir("templates")]
	model_files = ["models/" + x for x in safeListdir("models")]
	#app.run(debug=True, host="0.0.0.0", extra_files=template_files + model_files)
	app.run(debug=True, host="0.0.0.0", extra_files=model_files)