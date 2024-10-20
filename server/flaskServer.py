from flask import Flask, Response, jsonify, request
import cv2
import numpy as np
import tensorflow as tf
from ultralytics import YOLO
import copy#Para copiar las metricas
from collections import defaultdict#Para almacenar las id's
#Cargar usuario y contraseña de la camara
from dotenv import load_dotenv
import os
#Crear multiprocesos para no saturar las funciones
import queue
q=queue.Queue(maxsize=2)#Crear queue para pasar los frames entre multiprocesos
import threading

#pip install flask opencv-python-headless tensorflow ultralytics python-dotenv [torch, solo si flask lo pide]
#Necesita un modelo .h5 que pesa mas del limite de github, descargar para probar

app = Flask(__name__)

# Definir colores para cada estado de engagement (BGR)
colorList = {
    "Engaged": (94, 197, 34),  # Verde claro
    "Frustrated": (68, 68, 239),   # Rojo
    "Confused": (22, 115, 249),   # Naranjo
    "Bored": (246, 130, 59)     # Celeste
}

#Metricas
metrics = {} #Metricas locales, se modificaran aqui (flask); Se va definiendo en displayFrames() ya que de todas formas se tendria que vaciar el json en cada iteracion
metricsAPI = {}#Metricas que se enviaran al frontend, copiara el contenido de "metrics" cuando se termine de procesar el frame

#DAISEE
daiseeLabels = ["Frustrated", "Confused", "Bored", "Engaged"]
minConfidence = 0.3#umbral minimo de confianza

#Cargar modelos
engagementModel = tf.keras.models.load_model("modelo_cnn_knn.h5") #Modelo cnn
yoloModel = YOLO('yolov8n-face.pt')# #Modelo yolo, cambiar a yolov8n-face.pt si solo se quiere detectar rostros
#device = 'cuda' if torch.cuda.is_available() else 'cpu' #Cargar el modelo en la GPU si esta disponible
yoloModel = yoloModel.to('cpu')#device

#Contador de ID's
personIdCounter = 1
activePersonIds = {}

#Lista de ip que sirven para pruebas
#http://162.191.81.11:81/cgi-bin/mjpeg?resolution=800x600&quality=1&page=1725548701621&Language=11
#http://129.125.136.20/mjpg/video.mjpg?resolution=800x600&quality=1&page=1725548701621&Language=11

#Datos de la camara
load_dotenv()
userCam = os.getenv('CAMERAUSER')
passCam = os.getenv('CAMERAPASS')
camLink = "TestVideos/3.mp4"#f"rtsp://{userCam}:{passCam}@192.168.100.84:554/av_stream/ch0"
cap = cv2.VideoCapture(camLink)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  #Cantidad de fotogramas que se almacenaran en el buffer
processVideo = False#Determina si el video se procesara o no

#Reducir la carga de la CPU haciendo ajustes en la transmision
fpsTarget = 1#Cantidad de fps que se quiere procesar
frameCount = 0
fpsStream = 0#FPS de la transmision

#Resolucion del stream
resWidth = 1920
resHeight = 1080

if not cap.isOpened():
    raise Exception("Error: Could not open video stream.")
else:
    #print("CUDA:", torch.cuda.is_available())
    #if torch.cuda.is_available():
    #    print("Número de GPUs:", torch.cuda.device_count())
    #    print("Nombre de la GPU:", torch.cuda.get_device_name(0))
    print("\n///////\nstream in http://127.0.0.1:5001/video_feed \n Metrics: http://127.0.0.1:5001/metrics \n///////\n")

#Limpiar el contador de ID cuando no se detecten mas personas en un frame
def resetIDCounter():
    global personIdCounter, activePersonIds
    personIdCounter = 1
    activePersonIds = {}

#Recibir transmision desde la camara y enviarla a displayFrames
def receiveStream():
    global frameCount, fpsStream
    cap = cv2.VideoCapture(camLink)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  #Cantidad de fotogramas que se almacenaran en el buffer
    #Intenta cambiar la resolucion desde la fuente de video (algunos dispositivos pueden no permitir un cambio en la resolucion)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, resWidth)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, resHeight)
    ret, frame = cap.read()
    #Si el dispositivo no admite el cambio de resolucion con set (ej: rtsp), cambiar la resolucion manualmente
    if (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) != resWidth and int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) != resHeight):
        frame = cv2.resize(frame, (resWidth, resHeight))#Tamaño de entrada (debe coincidir con la redimension dentro del while)
    q.put(frame)
    
    while True:#Evita que el Thread finalice
        ret, frame = cap.read()
        if not ret:
            print("receiveStream() not RET")
            #Intentar una reconexion
            cap.release()
            cap = cv2.VideoCapture(camLink)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  #Cantidad de fotogramas que se almacenaran en el buffer
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, resWidth)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, resHeight)
            continue

        #Poner los fps del stream en la imagen a enviar
        fps = cap.get(cv2.CAP_PROP_FPS)
        fpsStream = fps

        #FPS del stream
        cv2.putText(frame, f'FPS: {fps}', (30, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, [0,0,0], 2)

        ##Redimensionar el frame si no cumple con la resolucion deseada
        if (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) != resWidth and int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) != resHeight):
            frame = cv2.resize(frame, (resWidth, resHeight))
        
        #Enviar los frames a displayFrames()
        q.put(frame)

#Recibir frames de receiveStream, procesarlos en yolo y enviarlos a la API
def displayFrames():
    global metricsAPI, personIdCounter, activePersonIds,frameCount
    while True:#Evita que el Thread finalice
        while processVideo:
            if q.empty() !=True:
                frame=q.get()#Recibir los frames de "receiveStream"

                #region procesar frames en yolo
                #Para no aumentar la carga de la cpu, solo procesar x frames por segundo
                frameCount+=1

                #Verifica si el buffer se esta llenando
                if q.qsize() > 1:
                    print("Skipping due to buffer")
                    continue

                if frameCount % (fpsStream // fpsTarget) != 0:
                    print("Frame count skip")
                    continue
                frameCount = 0
                
                #Establecer metricas locales
                metrics["totalPeople"] = 0
                metrics["stateCounts"] = {"Frustrated": 0, "Confused": 0, "Bored": 0, "Engaged": 0}
                metrics["Ids"] = {}
                
                #Mover el frame a GPU/CPU
                #frameTensor = torch.from_numpy(frame).to(device)

                # deteccion de objetos de YOLO
                results = yoloModel.track(frame, persist=True, classes=0)#track y persist=True para asignar id a lo identificado, classes=0 para personas
                metrics["totalPeople"] = sum(1 for det in results[0].boxes if det.cls[0] == 0) #Contar personas detectadas (para comprobar que la suma de los estados es correcta)
                if results and len(results[0].boxes) > 0:
                    personDetected = False #Resetear verificador de personas por frame
                    for detection in results[0].boxes:
                        if detection.id is not None:
                            personDetected = True#Persona detectada
                            yoloTrackID = int(detection.id.item())

                            #Si el iD de yolo no esta en mi variable customisada, asignar una
                            if yoloTrackID not in activePersonIds:
                                activePersonIds[yoloTrackID] = personIdCounter
                                personIdCounter +=1

                            #Obtenemos el ID personalizado de la persona
                            trackID = activePersonIds[yoloTrackID]

                            #Coordenadas para el boundbox
                            x1, y1, x2, y2 = map(int, detection.xyxy[0])

                            face = frame[y1:y2, x1:x2]
                            if face.size == 0:#Si el tamaño de algun rostro detectado es 0, saltar al siguiente frame
                                continue

                            faceResized = cv2.resize(face, (224, 224))
                            faceArray = np.expand_dims(faceResized, axis=0) / 255.0

                            #Prediccion de estado
                            engagementPrediction = engagementModel.predict(faceArray)

                            if engagementPrediction.ndim == 2 and engagementPrediction.shape[1] == len(daiseeLabels):
                                predictedIndex = np.argmax(engagementPrediction[0])#[0] por que engagementPrediction es un array doble [[x,x,x,x]]
                                predictedProbabilities = engagementPrediction[0][predictedIndex]#Extraer las probabilidades

                                #Asignar un estado dependiendo del umbral de confianza (si el % de confianza de la prediccion es menor al minimo, se detectara por defecto "Engaged"")
                                if predictedProbabilities > minConfidence:
                                    engagementState = daiseeLabels[predictedIndex]
                                else:
                                    #Si no cumplio el umbral de confianza, continuar al siguiente frame y no dibujar el boundbox
                                    continue

                                #Agregar el contador de estado
                                metrics["stateCounts"][engagementState] += 1
                                #Agregar los ids al json
                                metrics["Ids"][trackID] = {}#Establecer formato
                                metrics["Ids"][trackID]["confidence"] = round(predictedProbabilities*100)#Temporalmente agrego el porcentaje de probabilidad
                                metrics["Ids"][trackID]["state"] = engagementState

                                #Seleccionar el color correspondiente
                                color = colorList.get(engagementState, (255, 255, 255))  # Blanco por defecto si no se encuentra

                                #Bound box
                                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

                                #Texto de estado + % de probabilidad
                                cv2.putText(frame, f'ID: {trackID} | {engagementState} %{round(predictedProbabilities*100)}', (x1, y1 - 10), 
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
                                    
                                    

                    #Si no hay personas en la imagen, resetear ID
                    if not personDetected:
                        resetIDCounter()
                else:
                    #Si no hay resultados o boxes (aunque este no filtra por personas)
                    resetIDCounter()
                
                #endregion

                #region Enviar los frames a la pantalla

                #Convertir el frame a jpg
                ret, buffer = cv2.imencode('.jpg', frame)
                if not ret:
                    continue
                
                #Actualizar las metricas solo cuando se haya terminado de procesar el frame
                metricsAPI = copy.deepcopy(metrics)

                frame = buffer.tobytes()
                yield (b'--frame\r\n'
                                b'Content-Type:image/jpeg\r\n'
                                b'Content-Length: ' + f"{len(frame)}".encode() + b'\r\n'
                                b'\r\n' + frame + b'\r\n')
                #endregion

#Ruta del video en stream
@app.route('/video_feed')
def video_feed():
    return Response(displayFrames(), mimetype='multipart/x-mixed-replace; boundary=--frame')

#Enviar las metricas a express
@app.route('/metrics', methods=('GET',))
def getMetrics():
    return jsonify(metricsAPI)

#Ver la confianza
@app.route('/getConfidence', methods=['GET'])
def getConfidence():
    return jsonify(minConfidence)

#Modificar la confianza desde express
@app.route('/setConfidence', methods=['POST'])
def setConfidence():
    global minConfidence
    try:
        # Obtener el nuevo umbral de confianza desde el cuerpo de la petición
        data = request.get_json()
        newConfidence = float(data.get('minConfidence'))

        # Verificar que el valor esté en un rango válido (0.0 a 1.0)
        if 0 <= newConfidence <= 1:
            minConfidence = newConfidence
            return jsonify({"status": "success", "new_confidence": minConfidence}), 200
        else:
            return jsonify({"status": "error", "message": "Confidence must be between 0 and 1"}), 400

    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid confidence value"}), 400

#Para evitar desincronizacion, terminar de procesar un video
@app.route('/setVideoStream', methods=['POST'])
def setProcessVideo():
    global processVideo
    try:
        data = request.get_json()
        newState = bool(data.get('processVideo'))

        processVideo = newState
        return jsonify({"status": "success", "newState": processVideo}), 200
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "Invalid video state value"}), 400

if __name__ == "__main__":
    #Crear hilos para no sobrecargar un proceso recibiendo y procesando frames
    p1=threading.Thread(target=receiveStream)
    p2 = threading.Thread(target=displayFrames)
    p1.daemon = True#Los hilos terminaran cuando la funcion principal (flask) termine
    p2.daemon = True
    p1.start()
    p2.start()
    #Abrir servidor de flask
    app.run(host='127.0.0.1', port=5001, debug=False)