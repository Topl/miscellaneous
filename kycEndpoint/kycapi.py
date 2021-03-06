# Main application for serving the Topl presale
#
# This product includes GeoLite2 data created by MaxMind, available from
# <a href="http://www.maxmind.com">http://www.maxmind.com</a>.

from flask import Flask, request, jsonify, render_template, redirect, Response, session
from flask_cors import CORS
from functools import wraps
import flask_sqlalchemy
import hashlib
import geoip2.database
import toplEthTX
import traceback
import datetime
import os
import string
import random
import importlib
import jwt
import json

####################################################################################################################
# General variable declarations
# Define error log location


def formTime(ts): return ts.strftime("%Y.%m.%d_%H%M%S")


def errFilePath(ts): return './Logs/' + formTime(ts) + '_errorLog'


# Define the IDM form URL
idmURL = 'https://regtech.identitymind.store/viewform/'

# Define transaction lookup base URL
etherscan_url = 'https://etherscan.io/tx/'

# Define the geo-location IP data base file location
ipDB = geoip2.database.Reader('db/GeoLite2/GeoLite2-Country.mmdb')

# Define simple user authentication dictionary
topl_users = {
    "topl_admin": "7f4d69e38043ee58a81636b922993661b2e2f9fa4d0ba0127f94d74b7477860c",
    "topl_vip": "2913b3c9f6f1fdf3cc961aa0a46f8b1613e0a9175a6d38cc83cae8ec8ef79165"
}

# lambda function to shorten hash function call


def hash_func(str): return hashlib.sha256(str.encode('utf-8')).hexdigest()


# Define topl database location
project_dir = os.path.dirname(os.path.abspath(__file__))
database_file = "sqlite:///{}".format(os.path.join(
    os.path.sep, project_dir, 'db', 'topl_kyc_database.db'))

####################################################################################################################
# Flask app setup
# standard instantiantion of the api application through flask
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = database_file
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = os.urandom(20)

# applies the Access-Control-Allow-Origin property to the api route as required by IDM
CORS(app, resources={r"/kyc": {"origins": "*"}})

# Setup database
db = flask_sqlalchemy.SQLAlchemy(app)

# Setup add_to_whitelist function based on environment
#eth_net = toplEthTX.Rinkeby() if app.env == 'production' else toplEthTX.Local()
eth_net = toplEthTX.Mainnet()

####################################################################################################################
# Database Models
# Database model for saving form data


class Participant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tid = db.Column(db.String(80), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    ip_addr = db.Column(db.String(15), nullable=False)
    kyc_result = db.Column(db.String(15), nullable=False)
    eth_addr = db.Column(db.String(45), nullable=False)
    user_id = db.Column(db.String(30), nullable=False)
    email = db.Column(db.String(80))
    addr_country = db.Column(db.String(2), nullable=False)
    doc_country = db.Column(db.String(2), nullable=False)
    tx_hash = db.Column(db.String(45), nullable=False)

    def __repr__(self):
        return "<id: {}, tid: {}, timestamp: {}, ip_addr: {}, kyc_result: {}, eth_addr: {}, user_id: {}, tx_hash: {}, email: {}, addr_country: {}>".format(
            self.id, self.tid, formTime(self.timestamp), self.ip_addr, self.kyc_result, self.eth_addr, self.user_id, self.tx_hash, self.email, self.addr_country)


class ToplAddr(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    address = db.Column(db.String(34), nullable=False)
    used = db.Column(db.Boolean, nullable=False)

    def __repr__(self):
        return "<id: {}, address: {}, used: {}>".format(self.id, self.address, self.used)

####################################################################################################################
# Function Defintions
# This function will verify and return the payload from the JWT
# Identity Mind public keys are available at https://regtech.identitymind.store/accounts/d/%20


def verifyJWT(req):
    # If request is Ajax based (from IDM) open their public key otherwise use the test
    pubKeyPath = 'idmProdPubKey.pem' if req.headers[
        'origin'] == 'https://regtech.identitymind.store' else 'toplPublicKey.pem'
    # Parse and verify JWT token
    reqJSON = req.get_json()
    with open('static/keys/' + pubKeyPath) as publicKey:
        return jwt.decode(reqJSON['jwtresponse'], publicKey.read(), algorithms='RS256')


def id_generator(size=6, chars=string.ascii_uppercase + string.digits):
    return ''.join(random.choice(chars) for _ in range(size))


def check_auth(username, password):
    """This function is called to check if a username / password combination is valid."""
    if username in topl_users:
        if topl_users[username] == hash_func(password):
            authBool = 1
        else:
            authBool = 0
    else:
        authBool = 0
    return authBool


def authenticate():
    """Sends a 401 response that enables basic auth"""
    return Response(
        'Could not verify your access level for that URL.\n'
        'You have to login with proper credentials', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'})


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

####################################################################################################################
# Flask Views - Production


@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def index(path):
    return render_template('index.html')

# setup the KYC route


@app.route("/kyc", methods=["GET", "POST"])
def kycProcess():
    if request.method == 'GET':
        return redirect('/kyc/general')
    else:
        try:
            # For troubleshooting when IDM send me data
            with open('Logs/access_log', 'a+') as a_log:
                a_log.write(formTime(datetime.datetime.utcnow()) + '\n')

            # verify and retrieve JSON from JWT
            payload = verifyJWT(request)

            # For troubleshooting what IDM sends me
            # with open('db/form_dump','a+') as f:
            #    f.write('\n\n' + json.dumps(payload, sort_keys=True, indent=4))

            # Get Ethereum address that should be assigned token rights
            if payload['form_data']['user_id'] == 'vip':
                # use fixed address for US investors
                if payload['form_data']['country'] == 'US':
                    usr_eth_addr = ToplAddr.query.get(1).address

                # use one of the generated address for non-US investors, then set used to True
                else:
                    addr_rec = ToplAddr.query.filter_by(used=False).first()
                    usr_eth_addr = addr_rec.address
                    addr_rec.used = True
                    db.session.commit()
            else:
                # return user input address
                usr_eth_addr = payload['form_data']['btc']

            # send KYC request via Infura API if KYC was accepted, (if manual_review, deny, or repeated then skip)
            if (usr_eth_addr != ToplAddr.query.get(1).address) and (payload['kyc_result'] == 'ACCEPT'):
                tx_hash = eth_net.add_to_whitelist(usr_eth_addr)
            else:
                tx_hash = 0

            # construct database object and save participant data
            db.session.add(Participant(
                tid=payload['tid'],
                ip_addr=request.remote_addr,
                kyc_result=payload['kyc_result'],
                eth_addr=usr_eth_addr,
                user_id=payload['form_data']['user_id'],
                tx_hash=tx_hash,
                email=payload['form_data']['email'],
                addr_country=payload['form_data']['country'],
                doc_country=payload['form_data']['docCountry']
            )
            )
            db.session.commit()

            return jsonify({"success": True})

        except Exception:
            # Handle exceptions to the process by creating a logfile with the full traceback
            with open(errFilePath(datetime.datetime.now()), 'a+') as errFile:
                errFile.write(traceback.format_exc())
            return jsonify({"success": False})

# route for updating the investor ethereum addresses


@app.route("/admin/uploadaddr", methods=['POST'])
@requires_auth
def upload():
    addr_list = request.get_json()
    # loop through dictionary and add to the db
    for iter in range(0, len(addr_list)):
        db.session.add(ToplAddr(
            address=addr_list[iter]['address'],
            used=True if iter == 0 else False
        )
        )
    db.session.commit()
    return jsonify({"success": True})


@app.route('/iconiq/registration', methods=['GET', 'POST'])
def iconiq_register():
    icnq_response = ''
    placeholder_addr = "0x0000000000000000000000000000000000000000"
    tx_url = ''

    if request.method == 'POST':
        try:
            # Catch exception in case the tx fails
            placeholder_addr = ''
            if eth_net.check_icnq_balance(request.form.get('eth_addr')) >= 100:
                # Enforce threshold of ICNQ token
                icnq_response = 'success'

                if eth_net.check_icnq_pro_rata(request.form.get('eth_addr')) > 0:
                    # Make sure this address isn't already registered
                    tx_url = etherscan_url + \
                        eth_net.set_iconiq_token_allotment(
                            request.form.get('eth_addr'))

            else:
                icnq_response = 'failure'

        except:
            placeholder_addr = 'Invalid address input. Please try again'

    return render_template('iconiq_registration.html',
                           disp_response=icnq_response, disp_addr=placeholder_addr, tx_url=tx_url)

# Form routes
# for serving the general population particpating in the sale
# If in US kick out to error page, if not allow to the KYC form


@app.route('/kyc/general')
def generalForm(ipBool=False):
    try:
        ipBool = ipDB.country(str(request.remote_addr)
                              ).country.iso_code == 'US'
    except:
        pass
    finally:
        if ipBool:
            return redirect('/ip_error')
        else:
            session_id = id_generator(10)
            session['session_id'] = session_id
            return render_template('form_host.html', iframeURL=(idmURL + "9ypwm/?user_id=" + session_id))

# for serving fiat investors through a slightly different form


@app.route('/kyc/vip')
@requires_auth
def investorForm():
    return render_template('form_host.html', iframeURL=(idmURL + "gxq27/?user_id=vip"))

@app.route('/identity')
@requires_auth
def typeformForm():
    return render_template('form_host.html', iframeURL="https://topl.typeform.com/to/eZHrS7") 

# Result pages


@app.route('/result/accept')
def accept():
    try:
        tx_url = etherscan_url + \
            Participant.query.filter_by(
                user_id=session.get('session_id')).first().tx_hash
    except Exception:
        tx_hash = ''
        tx_url = ''
    # Handle exceptions to the process by creating a logfile with the full traceback
        with open(errFilePath(datetime.datetime.now()), 'a+') as errFile:
            errFile.write(traceback.format_exc())
    finally:
        return render_template('accept.html', tx_url=tx_url)


@app.route('/result/accept-vip')
def accept_vip():
    return render_template('accept-vip.html')


@app.route('/result/review')
def review():
    return render_template('review.html')


@app.route('/result/deny')
def deny():
    return render_template('deny.html')

# Intro walkthrough


@app.route('/intro-to-ethereum')
def intro():
    return render_template('intro_to_ethereum.html')

# Error page for IP address in US


@app.route('/ip_error')
def ip_error():
    return render_template('ip-error.html')

# Test form routes


@app.route('/testindex')
@requires_auth
def testindex():
    return render_template('index.html')


@app.route('/testform/home')
@requires_auth
def test_home():
    return render_template('test_home.html')


@app.route('/testform/general')
@requires_auth
def test_generalForm():
    return render_template('form_host.html', iframeURL=(idmURL + "gyeq4/?user_id=general"))


@app.route('/testform/vip')
@requires_auth
def test_investorForm():
    return render_template('form_host.html', iframeURL=(idmURL + "6wquv/?user_id=vip"))

# Asset routes
@app.route('/images/participate.png')
def get_images_participate():
    return app.send_static_file('img/participate.png')


@app.route('/images/balance.png')
def get_images_balance():
    return app.send_static_file('img/balance.png')


@app.route('/images/use-tokens.png')
def get_images_usetokens():
    return app.send_static_file('img/use-tokens.png')

@app.route('/images/piggy-bank.png')
def get_images_calculator():
    return app.send_static_file('img/piggy-bank.png')

@app.route('/static/media/background.e9091fe5.png')
def get_images_background():
    return app.send_static_file('img/background.png')


@app.route('/static/media/topl_logo_white.66d9ce72.svg')
def get_images_topl_logo():
    return app.send_static_file('img/topl_logo_white.svg')

@app.errorhandler(404)
def page_not_found(e):
    # note that we set the 404 status explicitly
    return render_template('404.html'), 404


if __name__ == '__main__':
    app.run(host=('0.0.0.0' if app.env == 'production' else '127.0.0.1'))
