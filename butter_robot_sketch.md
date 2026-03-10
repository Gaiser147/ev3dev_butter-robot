## butter roboter
### hardware 
- raspberry pi 5 (4gb) 
- lego mindstorms controller (ev3dev firmware)
- ai hailo+  hat (26TOPS)
- libcam (raspberry cam)

### Install flow
- Raspberry pi with ubuntu 
- libcam libaries compiled from source
- ai hailo hat firmware compiled from source and updated
- ai model dataset (butter detection) created on roboflow
- YOLOv11 trained with dataset on desktop pc (ubunut)
- AI model converted from onnx to hef on desktop pc (ubuntu) with hailo developer suite
- ev3dev updated and connected to raspberry pi 5(4gb) via mini usb cable
- rypc installed on ev3dev and raspberry pi 5 (4gb)
- rypc server started on ev3dev 
- set usb rules and tehtering for ev3dev on raspberry pi 5 (4gb)
- raspberry pi 5 (4gb) connected to rypc server (ev3dev)
- python skript running on raspberry pi 5 (4gb) and calling ev3dev for moments

### Programm function
- robot starts hailo moduel is initialized (with hef model) 
- robot turns (with stops in between) until butter is detected
- if butter seen, robot drives torwards it, if its not butter search (turn) starts again
- when butter locked (confidence >75) robot advances further towards, lowers arms (for collecting butter) advances to butter until butter is lost (under robot)
- robot raises arms (butter should be on top of arms,  because robot shoved arms under butter) and "says" "butter found"
