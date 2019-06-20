# Qualisys Lab Streaming Layer App
Stream Qualisys Track Manager (QTM) 3D and 6DOF data as Lab Streaming Layer (LSL) Motion Capture (MoCap) data.

## Dependencies
- [Qualisys SDK for Python](https://github.com/qualisys/qualisys_python_sdk)
- Python interface to LSL [pylsl](https://github.com/labstreaminglayer/liblsl-Python)

## Installation
Ensure Python 3.5 or above is installed.

1. Upgrade to latest pip
```
python -m pip install pip
```
2. Install dependencies
```
python -m pip install -r "./requirements.txt"
```
3. (Optional) Install dev dependencies to be able to run the tests
```
python -m pip install -r "./requirements-dev.txt"
```

## Usage
1. Ensure QTM is running either locally or on an external host that you are able to ping
2. Start LSL App
```
python gui.py
```
3. Enter address, port, and start
> ![qtm_lsl_init.PNG](images/qtm_lsl_init.PNG)

Once started, the app is either in a waiting or streaming state. Waiting means QTM is not yet streaming data and so there is no LSL stream open. Once QTM starts streaming, the LSL stream is opened.
> ![qtm_lsl_wait.PNG](images/qtm_lsl_wait.PNG)
![qtm_lsl_stream.PNG](images/qtm_lsl_stream.PNG)
