from django.shortcuts import render, redirect
from django.template.loader import render_to_string
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from .models import *
from .forms import *
from django.core.files.base import ContentFile
import sys
import socket
import io
import codecs
import psutil
from datetime import datetime, timezone
# Extremely hacky fix for VTK not importing correctly on Heroku
try:
    from shutil import copyfile
    initSrc = "./application/scripts/__init__.py"
    initDst = ".heroku/python/lib/python3.7/site-packages/vtk/__init__.py"
    copyfile(initSrc, initDst)

except OSError:
    pass

from .visualizerDVH import dose_volume_histogram as dvh
from .visualizer3D import visualizer
from application.tclGenerator import *
from django.contrib.auth.views import LoginView
from django.contrib.auth.views import LogoutView
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import login, authenticate
from application.forms import SignUpForm
import paramiko
from django.db import models, connections
from django.db.utils import DEFAULT_DB_ALIAS, load_backend
from application.storage_backends import *
from django.core.files.storage import default_storage
from django.core import serializers
import time

from .tokens import account_activation_token
from django.contrib.sites.shortcuts import get_current_site
from django.utils.encoding import force_bytes, force_text
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.template.loader import render_to_string

from django.conf import settings
from django.core.mail import send_mail
from django.contrib import messages
from django.core.mail import EmailMessage
from django.views.generic.detail import DetailView   
from django.http import FileResponse

from decouple import config
from multiprocessing import Process, Event
import threading
import select
#send_mail('Subject here', 'Here is the message.', settings.EMAIL_HOST_USER,
 #        ['to@example.com'], fail_silently=False)

# Create your views here.
class BaseFileDownloadView(DetailView):
    def get(self, request, *args, **kwargs):
        filename=self.kwargs.get('filename', None)
        originalfilename=self.kwargs.get('originalfilename', None)
        if filename is None:
            raise ValueError("Found empty filename")
        
        try:
            some_file = default_storage.open(filename)
        except:
            return HttpResponse("The file you are requesting does not exist on the server.")
        response = FileResponse(some_file)
        # https://docs.djangoproject.com/en/1.11/howto/outputting-csv/#streaming-large-csv-files
        if originalfilename is not None:
            response['Content-Disposition'] = 'attachment; filename="%s"'%originalfilename
        else:
            response['Content-Disposition'] = 'attachment; filename="%s"'%filename
        return response

class fileDownloadView(BaseFileDownloadView):
    pass

# homepage
def home(request):
    return render(request, "home.html")

# FullMonte Tutorial page
def fmTutorial(request):
    return render(request, "tutorial.html")

# FullMonte About page
def about(request):
    return render(request, "about.html")

#Please login page - when trying to access simulator
def please_login(request):
    return render(request, "please_login.html")

# FullMonte Simulator start page
def fmSimulator(request):
    # First check if user is logged-in
    if not request.user.is_authenticated:
        return redirect('please_login')

    # Check if EC2 instance is set up in the current session
    try:
        dns = request.session['DNS']
        text_obj = request.session['text_obj']
        tcpPort = request.session['tcpPort']
    except:
        messages.error(request, 'Error - please connect to an AWS remote server before trying to simulate')
        return HttpResponseRedirect('/application/aws')
    #print(22222)
    # if this is a POST request we need to process the form data
    if request.method == 'POST':
        #print(11111)
        #print(request.POST)
        #print(request.FILES)
        sys.stdout.flush()
        form = tclInputForm(data=request.POST, files=request.FILES)

        # check whether it's valid:
        if form.is_valid():
            print(form.cleaned_data)
            sys.stdout.flush()
            if request.POST['selected_existing_meshes'] != "":
                print("This is 1")
                # selected a mesh from database
                mesh_from_database = meshFiles.objects.filter(id=request.POST['selected_existing_meshes'])[0]

                obj = form.save(commit = False)
                obj.meshFile = mesh_from_database.meshFile
                obj.originalMeshFileName = mesh_from_database.originalMeshFileName
                obj.meshFileID = mesh_from_database
                obj.user = request.user
                obj.save()
            else:
                print("This is 2")
                # uploaded a new mesh
                # process cleaned data from formsets
                obj = form.save(commit = False)
                obj.user = request.user
                obj.originalMeshFileName = obj.meshFile.name
                obj.save()
                print(obj)
                sys.stdout.flush()

                # create entry for the newly uploaded meshfile
                new_mesh_entry = meshFiles()
                new_mesh_entry.meshFile = obj.meshFile
                new_mesh_entry.originalMeshFileName = obj.originalMeshFileName
                new_mesh_entry.user = request.user
                new_mesh_entry.save()

                # update meshfile id
                obj.meshFileID = new_mesh_entry
                obj.save()

            request.session['meshUnit'] = form.cleaned_data['meshUnit']
            request.session['kernelType'] = form.cleaned_data['kernelType']
            request.session['scoredVolumeRegionID'] = form.cleaned_data['scoredVolumeRegionID']
            request.session['packetCount'] = form.cleaned_data['packetCount']
            request.session['totalEnergy'] = form.cleaned_data['totalEnergy']
            request.session['energyUnit'] = form.cleaned_data['energyUnit']

            return HttpResponseRedirect('/application/simulator_material')

    # If this is a GET (or any other method) create the default form.
    else:
        form = tclInputForm(CUDA=request.session['GPU_instance'])

    uploaded_meshes = meshFiles.objects.filter(user=request.user)

    context = {
        'form': form,
        'aws_path': request.session['DNS'],
        'port': request.session['tcpPort'],
        'uploaded_meshes': uploaded_meshes,
    }

    return render(request, "simulator.html", context)

# FullMonte Simulator material page
def fmSimulatorMaterial(request):
    # First check if user is logged-in
    if not request.user.is_authenticated:
        return redirect('please_login')

    # if this is a POST request we need to process the form data
    if request.method == 'POST':
        formset1 = materialSetSet(request.POST, form_kwargs={'mesh_unit': request.session['meshUnit']})

        # check whether it's valid:
        if formset1.is_valid():
            # process cleaned data from formsets

            request.session['material'] = []
            request.session['region_name'] = [] # for visualization legend
            request.session['scatteringCoeff'] = []
            request.session['absorptionCoeff'] = []
            request.session['refractiveIndex'] = []
            request.session['anisotropy'] = []

            for form in formset1:
                #print(form.cleaned_data)
                request.session['material'].append(form.cleaned_data['material'])
                request.session['region_name'].append(form.cleaned_data['material'])  # for visualization legend
                request.session['scatteringCoeff'].append(form.cleaned_data['scatteringCoeff'])
                request.session['absorptionCoeff'].append(form.cleaned_data['absorptionCoeff'])
                request.session['refractiveIndex'].append(form.cleaned_data['refractiveIndex'])
                request.session['anisotropy'].append(form.cleaned_data['anisotropy'])

            return HttpResponseRedirect('/application/simulator_source')

    # If this is a GET (or any other method) create the default form.
    else:
        formset1 = materialSetSet(request.GET or None, form_kwargs={'mesh_unit': request.session['meshUnit']})

    context = {
        'formset1': formset1,
        'unit': request.session['meshUnit'],
    }

    return render(request, "simulator_material.html", context)

# ajax requests
def ajaxrequests_view(request):
    ind = request.POST.get('ind', None)
    if(ind):
        ind = int(ind)
        get_data = Material.objects.filter(id=ind)
        ser_data = serializers.serialize("json", get_data)
        return HttpResponse(ser_data, content_type="application/json")
    else:
        return HttpResponse(None, content_type="application/json")

# developer page for creating new preset materials
def createPresetMaterial(request):
    presetMaterial = Material.objects.all()

    # if this is a POST request we need to process the form data
    if request.method == 'POST':
        if 'reset' in request.POST:
            Material.objects.all().delete()
            form = materialForm(request.POST)
        else:
            form = materialForm(request.POST, request.FILES)

            # check whether it's valid:
            if form.is_valid():
                # process cleaned data from formsets
                #print(form.cleaned_data)

                form.save()
                messages.success(request, 'Material added successfully, you can now see it in table below')

                return redirect("create_preset_material")

            else:
                messages.error(request, 'Failed to add material, material values must be within bounds')

                return redirect("create_preset_material")

    else:
        form = materialForm(request.GET)

    context = {
        'form': form,
        'presetMaterials': presetMaterial,
    }
    return render(request, "create_preset_material.html", context)

# FullMonte Simulator light source page
def fmSimulatorSource(request):
    # First check if user is logged-in
    if not request.user.is_authenticated:
        return redirect('please_login')

    # visualize input mesh
    # temporily transfer input mesh to Ec2 instance
    inputMeshFileName = tclInput.objects.filter(user = request.user).latest('id').meshFile.name
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    text_obj = request.session['text_obj']
    private_key_file = io.StringIO(text_obj)
    privkey = paramiko.RSAKey.from_private_key(private_key_file)
    client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey, timeout=10)
    ftp = client.open_sftp()
    ftp.chdir('docker_sims/')
    # transfer mesh in chunks to save memory
    with ftp.open('./'+inputMeshFileName, 'wb') as ftp_file:
        with default_storage.open(inputMeshFileName) as mesh_file:
            for piece in mesh_file.chunks(chunk_size=32*1024*1024):
                ftp_file.write(piece)
    ftp.close()
    
    # generate ParaView Visualization URL
    # e.g. http://ec2-35-183-12-167.ca-central-1.compute.amazonaws.com:8080/
    dns = request.session['DNS']
    tcpPort = request.session['tcpPort']
    visURL = "http://" + dns + ":" + tcpPort + "/"
    # render 3D visualizer
    text_obj = request.session['text_obj']
    p = Process(target=visualizer, args=(inputMeshFileName, True, dns, tcpPort, text_obj, ))
    p.start()

    # if this is a POST request we need to process the form data
    if request.method == 'POST':
        formset2 = lightSourceSet(request.POST)
        print(request.POST)
        sys.stdout.flush()


        # check whether it's valid:
        if formset2.is_valid():
            # process cleaned data from formsets

            request.session['sourceType'] = []
            request.session['xPos'] = []
            request.session['yPos'] = []
            request.session['zPos'] = []
            request.session['xDir'] = []
            request.session['yDir'] = []
            request.session['zDir'] = []
            request.session['vElement'] = []
            request.session['rad'] = []
            request.session['power'] = []
            request.session['volumeRegion'] = []
            request.session['emitHemiSphere'] = []
            request.session['hemiSphereEmitDistribution'] = []
            request.session['numericalAperture'] = []
            request.session['checkDirection'] = []
            request.session['xDir1'] = []
            request.session['yDir1'] = []
            request.session['zDir1'] = []
            request.session['xPos0'] = []
            request.session['yPos0'] = []
            request.session['zPos0'] = []
            request.session['xPos1'] = []
            request.session['yPos1'] = []
            request.session['zPos1'] = []
            request.session['emitVolume'] = []

            for form in formset2:
                print(form.cleaned_data)
                request.session['sourceType'].append(form.cleaned_data['sourceType'])
                request.session['xPos'].append(form.cleaned_data['xPos'])
                request.session['yPos'].append(form.cleaned_data['yPos'])
                request.session['zPos'].append(form.cleaned_data['zPos'])
                request.session['xDir'].append(form.cleaned_data['xDir'])
                request.session['yDir'].append(form.cleaned_data['yDir'])
                request.session['zDir'].append(form.cleaned_data['zDir'])
                request.session['vElement'].append(form.cleaned_data['vElement'])
                request.session['rad'].append(form.cleaned_data['rad'])
                request.session['power'].append(form.cleaned_data['power'])
                request.session['volumeRegion'].append(form.cleaned_data['volumeRegion'])
                request.session['emitHemiSphere'].append(form.cleaned_data['emitHemiSphere'])
                request.session['hemiSphereEmitDistribution'].append(form.cleaned_data['hemiSphereEmitDistribution'])
                request.session['numericalAperture'].append(form.cleaned_data['numericalAperture'])
                request.session['checkDirection'].append(form.cleaned_data['checkDirection'])
                request.session['xDir1'].append(form.cleaned_data['xDir1'])
                request.session['yDir1'].append(form.cleaned_data['yDir1'])
                request.session['zDir1'].append(form.cleaned_data['zDir1'])
                request.session['xPos0'].append(form.cleaned_data['xPos0'])
                request.session['yPos0'].append(form.cleaned_data['yPos0'])
                request.session['zPos0'].append(form.cleaned_data['zPos0'])
                request.session['xPos1'].append(form.cleaned_data['xPos1'])
                request.session['yPos1'].append(form.cleaned_data['yPos1'])
                request.session['zPos1'].append(form.cleaned_data['zPos1'])
                request.session['emitVolume'].append(form.cleaned_data['emitVolume'])
            
            mesh = tclInput.objects.filter(user = request.user).latest('id')

            script_path = tclGenerator(request.session, mesh, request.user)
            client.exec_command('rm -rf ~/docker_sims/'+inputMeshFileName)
            client.close()
            return HttpResponseRedirect('/application/simulation_confirmation')

    # If this is a GET (or any other method) create the default form.
    else:
        formset2 = lightSourceSet(request.GET or None)

    context = {
        'formset2': formset2,
        'unit': request.session['meshUnit'],
        'visURL': visURL,
    }

    client.close()
    return render(request, "simulator_source.html", context)

# FullMonte Simulator input confirmation page
def simulation_confirmation(request):
    class Optional_Tcl(forms.Form):
        tcl_file = forms.FileField(required=False)
    meshFilePath = tclInput.objects.filter(user = request.user).latest('id').meshFile.name
    generated_tcl = tclScript.objects.filter(user = request.user).latest('id')
    if request.method == 'POST':
        form = Optional_Tcl(request.POST, request.FILES)
        if not request.POST.__contains__('tcl_file'):
            # there is a file uploaded
            default_storage.delete(request.FILES['tcl_file'].name)
            default_storage.save(request.FILES['tcl_file'].name, request.FILES['tcl_file'])

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        text_obj = request.session['text_obj']
        private_key_file = io.StringIO(text_obj)
        
        privkey = paramiko.RSAKey.from_private_key(private_key_file)
        try:
            client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey, timeout=10)
        except:
            sys.stdout.flush()
            messages.error(request, 'Error - looks like your AWS remote server is down, please check your instance in the AWS console and connect again')
            return HttpResponseRedirect('/application/aws')

        client.exec_command('> ~/sim_run.log')
        client.close()
        connections.close_all()
        p = Process(target=transfer_files_and_run_simulation, args=(request, ))
        p.start()
        
        request.session['start_time'] = str(datetime.now(timezone.utc))
        request.session['started'] = "false"
        return HttpResponseRedirect('/application/running')
    
    class Material_Class:
        pass
    class Light_Source_Class:
        pass

    materials = []
    for i in range(len(request.session['material'])):
        temp = Material_Class()
        temp.layer = i + 1
        temp.material = request.session['material'][i]
        temp.scatteringCoeff = request.session['scatteringCoeff'][i]
        temp.absorptionCoeff = request.session['absorptionCoeff'][i]
        temp.refractiveIndex = request.session['refractiveIndex'][i]
        temp.anisotropy = request.session['anisotropy'][i]
        materials.append(temp)

    light_sources = []
    for i in range(len(request.session['sourceType'])):
        temp = Light_Source_Class()
        temp.source = i + 1
        temp.sourceType = request.session['sourceType'][i]
        temp.xPos = request.session['xPos'][i]
        temp.yPos = request.session['yPos'][i]
        temp.zPos = request.session['zPos'][i]
        temp.xDir = request.session['xDir'][i]
        temp.yDir = request.session['yDir'][i]
        temp.zDir = request.session['zDir'][i]
        temp.vElement = request.session['vElement'][i]
        temp.rad = request.session['rad'][i]
        temp.power = request.session['power'][i]
        temp.volumeRegion = request.session['volumeRegion'][i]
        temp.emitHemiSphere = request.session['emitHemiSphere'][i]
        temp.hemiSphereEmitDistribution = request.session['hemiSphereEmitDistribution'][i]
        temp.numericalAperture = request.session['numericalAperture'][i]
        temp.checkDirection = request.session['checkDirection'][i]
        temp.xDir1 = request.session['xDir1'][i]
        temp.yDir1 = request.session['yDir1'][i]
        temp.zDir1 = request.session['zDir1'][i]
        temp.xPos0 = request.session['xPos0'][i]
        temp.yPos0 = request.session['yPos0'][i]
        temp.zPos0 = request.session['zPos0'][i]
        temp.xPos1 = request.session['xPos1'][i]
        temp.yPos1 = request.session['yPos1'][i]
        temp.zPos1 = request.session['zPos1'][i]
        temp.emitVolume = request.session['emitVolume'][i]
        light_sources.append(temp)
    
    tcl_form = Optional_Tcl()

    context = {
        'mesh_name': meshFilePath, 
        'materials': materials,
        'light_sources': light_sources,
        'tcl_script_name': generated_tcl.script.name,
        'tcl_form': tcl_form,
        'unit': request.session['meshUnit'],
    }

    return render(request, 'simulation_confirmation.html', context)

def transfer_files_and_run_simulation(request):
    conn = create_connection()
    conn.ensure_connection()
    meshFilePath = tclInput.objects.filter(user = request.user).latest('id').meshFile.name
    generated_tcl = tclScript.objects.filter(user = request.user).latest('id')
    tcl_file = default_storage.open(generated_tcl.script.name)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    text_obj = request.session['text_obj']
    private_key_file = io.StringIO(text_obj)
    privkey = paramiko.RSAKey.from_private_key(private_key_file)
    client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey, timeout=10)
    ftp = client.open_sftp()

    ftp.chdir('docker_sims/')
    file=ftp.file('docker.sh', "w")
    file.write('#!/bin/bash\ncd sims/\ntclmonte.sh ./'+generated_tcl.script.name)
    file.flush()
    ftp.chmod('docker.sh', 700)

    # transfer mesh in chunks to save memory
    with ftp.open('./'+meshFilePath, 'wb') as ftp_file:
        with default_storage.open(meshFilePath) as mesh_file:
            for piece in mesh_file.chunks(chunk_size=32*1024*1024):
                ftp_file.write(piece)

    ftp.putfo(tcl_file, './'+generated_tcl.script.name)
    ftp.close()

    if request.session['GPU_instance']:
        # add an argument to add nvidia runtime for gpu
        command = "sudo ~/docker_sims/FullMonteSW_setup.sh 1 > ~/sim_run.log" 
    else:
        command = "sudo ~/docker_sims/FullMonteSW_setup.sh > ~/sim_run.log"
    client.exec_command(command)
    client.close()
    conn.close()

# https://stackoverflow.com/questions/56733112/how-to-create-new-database-connection-in-django
def create_connection(alias=DEFAULT_DB_ALIAS):
    connections.ensure_defaults(alias)
    connections.prepare_test_settings(alias)
    db = connections.databases[alias]
    backend = load_backend(db['ENGINE'])
    return backend.DatabaseWrapper(db, alias)

# Output mesh upload page
def visualization_mesh_upload(request):
    if request.method == 'POST':
        print(request)
        form = visualizeMeshForm(request.POST, request.FILES)
        if form.is_valid():
            print(form.cleaned_data)
            # get mesh file from form
            obj = form.save(commit = False)
            obj.user = request.user
            obj.save()
            uploadedOutputMeshFile = visualizeMesh.objects.filter(user = request.user).latest('id')
            outputMeshFileName = uploadedOutputMeshFile.outputMeshFile.name
            outputMeshFile = default_storage.open(outputMeshFileName)
            print(outputMeshFileName)

            # copy mesh into remote server
            text_obj = request.session['text_obj']
            private_key_file = io.StringIO(text_obj)
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            privkey = paramiko.RSAKey.from_private_key(private_key_file)
            try:
                client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey, timeout=10)
            except:
                sys.stdout.flush()
                messages.error(request, 'Error - looks like your AWS remote server is down, please check your instance in the AWS console and connect again')
                return HttpResponseRedirect('/application/aws')

            sftp = client.open_sftp()
            sftp.chdir('docker_sims/')
            sftp.putfo(outputMeshFile, './'+outputMeshFileName)
            sftp.close()
            client.close()
            
            # save mesh file info for visualization
            info = meshFileInfo.objects.filter(user = request.user).latest('id')
            info.fileName = outputMeshFileName
            info.dvhFig = "<p>Dose Volume Histogram not yet generated</p>"
            info.save()

            # TODO: parse out fluence energy unit from mesh and save it
            request.session['fluenceEnergyUnit'] = "(unit not provided in mesh file)"

            # set material list to empty because the list is only used for mesh visualizaition from simulation.
            # uploaded mesh files do not have material information provided, so they will not have material names in legend
            request.session['region_name'] = []
            return HttpResponseRedirect('/application/visualization')
    else:
        form = visualizeMeshForm(request.GET or None)
    context = {
        'form': form,
    }
    return render(request, "mesh_upload.html", context)

# FullMonte output Visualization - generate DVH
def fmVisualization(request):
    # First check if user is logged-in
    if not request.user.is_authenticated:
        return redirect('please_login')

    try:
        dns = request.session['DNS']
        text_obj = request.session['text_obj']
        tcpPort = request.session['tcpPort']
    except:
        messages.error(request, 'Error - please connect to an AWS remote server before trying to visualize')
        return HttpResponseRedirect('/application/aws')

    info = meshFileInfo.objects.filter(user = request.user).latest('id')
    outputMeshFileName = info.fileName
    if len(outputMeshFileName) == 0:
        messages.error(request, 'Error - please run simulation or upload a mesh before trying to visualize')
        return HttpResponseRedirect('/application/mesh_upload')

    # first, try to connect to remote server
    private_key_file = io.StringIO(text_obj)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    privkey = paramiko.RSAKey.from_private_key(private_key_file)
    try:
        client.connect(dns, username='ubuntu', pkey=privkey, timeout=10)
    except:
        sys.stdout.flush()
        messages.error(request, 'Error - looks like your AWS remote server is down, please check your instance in the AWS console and connect again')
        return HttpResponseRedirect('/application/aws')

    # check if file exists in the remote server
    sftp = client.open_sftp()
    try:
        sftp.stat('docker_sims/'+outputMeshFileName)
        info.remoteFileExists = True
    except:
        info.remoteFileExists = False
    sftp.close()
    client.close()
    sys.stdout.flush()
    info.save()

    # file exists for DVH
    if(info.remoteFileExists):
        # generate DVH
        if info.dvhFig == "<p>Dose Volume Histogram not yet generated</p>":
            print('generating DVH')
            print('before')
            current_process = psutil.Process()
            children = current_process.children(recursive=True)
            for child in children:
                print('Child pid is {}'.format(child.pid))
            connections.close_all()
            p = Process(target=dvh, args=(request.user, dns, tcpPort, text_obj, request.session['region_name'], ))
            p.start()
            print('after')
            current_process = psutil.Process()
            children = current_process.children(recursive=True)

            form = processRunning()
            form.user=request.user

            for child in children:
                form.pid = child.pid
                form.running = True
                print('Child pid is {}'.format(child.pid))

            conn = create_connection()
            conn.ensure_connection()
            form.save()
            conn.close()
            sys.stdout.flush()
            return HttpResponseRedirect('/application/runningDVH')
        # load saved DVH
        else:
            print('using last saved DVH')
            return HttpResponseRedirect('/application/displayVisualization')
    
    # DBH cannot be generated
    else:
        info.dvhFig = "<p>Could not generate Dose Volume Histogram</p>"
        info.save()
        return HttpResponseRedirect('/application/displayVisualization')


# Running DVH progress page
def runningDVH(request):
    running_process = processRunning.objects.filter(user = request.user).latest('id')
    if running_process.running:
        start_time = running_process.start_time
        current_time = datetime.now(timezone.utc)
        time_diff = current_time - start_time
        running_time = str(time_diff)
        running_time = running_time.split('.')[0]
        return render(request, "waitingDVH.html", {'time':running_time})
    else:
        return HttpResponseRedirect('/application/displayVisualization')


# page that loads both the 3D visualization and DVH
def displayVisualization(request):
    info = meshFileInfo.objects.filter(user = request.user).latest('id')
    outputMeshFileName = info.fileName
    fileExists = info.remoteFileExists
    dvhFig = info.dvhFig
    maxDose = info.maxFluence

    # generate ParaView Visualization URL
    # e.g. http://ec2-35-183-12-167.ca-central-1.compute.amazonaws.com:8080/
    dns = request.session['DNS']
    tcpPort = request.session['tcpPort']
    visURL = "http://" + dns + ":" + tcpPort + "/"

    # render 3D visualizer
    text_obj = request.session['text_obj']
    p = Process(target=visualizer, args=(outputMeshFileName, fileExists, dns, tcpPort, text_obj, ))
    p.start()

    # save history for dvh data if output mesh comes from simulation
    if (len(request.session['region_name']) > 0):
        history = simulationHistory.objects.filter(user=request.user).latest('id')
        conn = create_connection()
        conn.ensure_connection()

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        text_obj = request.session['text_obj']
        private_key_file = io.StringIO(text_obj)
        privkey = paramiko.RSAKey.from_private_key(private_key_file)
        try:
            client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey, timeout=10)
        except:
            sys.stdout.flush()
            messages.error(request, 'Error - looks like your AWS remote server is down, please check your instance in the AWS console and connect again')
            return HttpResponseRedirect('/application/aws')

        ftp = client.open_sftp()
        mesh_name = outputMeshFileName[:-8]
        output_csv_name = mesh_name + '.dvh.csv'
        output_png_name = mesh_name + '.dvh.png'
        
        try:
            output_csv_file = ftp.file('docker_sims/' + output_csv_name)
            output_png_file = ftp.file('docker_sims/' + output_png_name)
            history.output_dvh_csv_path.save(output_csv_name, output_csv_file)
            history.output_dvh_fig_path.save(output_png_name, output_png_file)
        except:
            print("Cannot save history for DVH data because file does not exist")

        ftp.close()
        client.close()
        history.save()
        conn.close()

    # get message
    if(fileExists):
        msg = "Using output mesh \"" + outputMeshFileName + "\" from the last simulation or upload."
    else:
        msg = "Mesh \"" + outputMeshFileName + "\" from the last simulation or upload was not found. Perhaps it was deleted. Root folder will be loaded for visualization."
    
    # pass message, DVH figure, and 3D visualizer link to the HTML
    context = {'message': msg, 'dvhFig': dvhFig, 'visURL': visURL, 'maxDose': maxDose, 'fluenceEnergyUnit': request.session['fluenceEnergyUnit']}
    return render(request, "visualization.html", context)

# page for diplaying info about kernel type
def kernelInfo(request):
    return render(request, "kernel_info.html")

# page for downloading preset values
def downloadPreset(request):    
    # First check if user is logged-in
    if not request.user.is_authenticated:
        return redirect('please_login')

    presetObjects = preset.objects.all()

    # if this is a POST request we need to process the form data
    if request.method == 'POST':
        if 'reset' in request.POST:
            preset.objects.all().delete()
            form = presetForm(request.POST)
        else:
            form = presetForm(request.POST, request.FILES)

            # check whether it's valid:
            if form.is_valid():
                # process cleaned data from formsets
                #print(form.cleaned_data)

                form.save()
                messages.success(request, 'Mesh added successfully, you can now see it in table below')

                return redirect("download_preset")

    # If this is a GET (or any other method) create the default form.
    else:
        form = presetForm(request.GET)

    context = {
        'form': form,
        'presetObjects': presetObjects,
    }

    return render(request, "download_preset.html", context)

# user account signup page
def signup(request):
    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = True
            user.save()
            #username = form.cleaned_data.get('username')
            #raw_password = form.cleaned_data.get('password1')
            #user = authenticate(username=username, password=raw_password)

            # disable activation email for now, until we find a better email server 
            """
            current_site = get_current_site(request)
            mail_subject = 'Activate your FullMonte account.'
            message = render_to_string('acc_active_email.html', {
                'user': user,
                'domain': current_site.domain,
                'uid':urlsafe_base64_encode(force_bytes(user.pk)),
                'token':account_activation_token.make_token(user),
            })
            to_email = form.cleaned_data.get('email')
            email = EmailMessage(
                        mail_subject, message, to=[to_email]
            )
            email.send()
            """
            login(request, user)
            return render(request, "activation_complete.html")

    else:
        form = SignUpForm()
    return render(request, 'signup.html', {'form': form})

# user account activation page
def activate(request, uidb64, token):
    try:
        uid = force_text(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except(TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None
    if user is not None and account_activation_token.check_token(user, token):
        user.is_active = True
        user.save()
        login(request, user)
        # return redirect('home')
        return render(request, "activation_complete.html")
    else:
        return HttpResponse('Activation link is invalid!')

# user acount info page
def account(request):
    # First check if user is logged-in
    if not request.user.is_authenticated:
        return redirect('please_login')
    return render(request, "account.html")

# user account changing passwords page
def change_password(request):
    # First check if user is logged-in
    if not request.user.is_authenticated:
        return redirect('please_login')

    # if this is a POST request we need to process the form data
    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, 'Your password has been changed successfully')
            return render(request, 'registration/change_password.html', {
                'form': form
    })
        else:
            messages.error(request, 'Please fix the shown error')
    else:
        form = PasswordChangeForm(request.user)
    return render(request, 'registration/change_password.html', {
        'form': form
    })

# error page for 30 second timeout when uploading/generating mesh files
# Heroku h12 timeout error
def heroku_timeout(request):
    return render(request, 'heroku_timeout.html')

# AWS EC2 instance setup page
def aws(request):
    # First check if user is logged-in
    if not request.user.is_authenticated:
        return redirect('please_login')
    
    # if this is a POST request we need to process the form data
    if request.method == 'POST':
        print(request)
        form = awsFiles(request.POST, request.FILES)
        print(request.POST.get("DNS"))
        print(request.POST.get("GPU_instance"))
        sys.stdout.flush()
        if form.is_valid():
            info = meshFileInfo() # prepare new mesh entry
            info.user = request.user
            info.save()
            print(form.cleaned_data)
            obj = form.save(commit = False)
            obj.user = request.user
            obj.save()
            request.session['DNS'] = form.cleaned_data['DNS']
            request.session['tcpPort'] = str(form.cleaned_data['TCP_port'])
            if request.POST.get("GPU_instance") == "True":
                request.session['GPU_instance'] = True
            else:
                request.session['GPU_instance'] = False
            print(request.session['GPU_instance'])
            sys.stdout.flush()
            # handle_uploaded_file(request.FILES['pemfile'])
            uploadedAWSPemFile = awsFile.objects.filter(user = request.user).latest('id')
            pemfile = uploadedAWSPemFile.pemfile

            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            private_key_file = io.BytesIO()
            for line in pemfile:
                private_key_file.write(line)
            private_key_file.seek(0)

            byte_str = private_key_file.read()
            text_obj = byte_str.decode('UTF-8')
            private_key_file = io.StringIO(text_obj)
            
            privkey = paramiko.RSAKey.from_private_key(private_key_file)
            request.session['text_obj'] = text_obj
            try:
                client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey, timeout=10)
            except:
                sys.stdout.flush()
                messages.error(request, 'Error - looks like your AWS remote server is down, please check your instance in the AWS console and connect again')
                return HttpResponseRedirect('/application/aws')
            
            sftp = client.open_sftp()
            need_setup = "false"

            try:
                sftp.stat('docker_sims/FullMonteSW_setup.sh')
            except IOError:
                need_setup = "true"

            if request.session['GPU_instance']:
                try:
                    sftp.stat('docker_sims/CUDA_setup.sh')
                except IOError:
                    need_setup = "true"
            
            try:
                sftp.stat('docker_pdt/pdt_space_setup.sh')
            except IOError:
                need_setup = "true"
            
            if need_setup == "true":
                # cluster that's not setup
                client.close()
            
                print('before')
                current_process = psutil.Process()
                children = current_process.children(recursive=True)
                for child in children:
                    print('Child pid is {}'.format(child.pid))
                connections.close_all()
                p = Process(target=run_aws_setup, args=(request, request.session['GPU_instance'], ))
                # print(p)
                p.start()
                print('after')
                current_process = psutil.Process()
                children = current_process.children(recursive=True)

                form = processRunning()
                form.user=request.user
                
                for child in children:
                    form.pid = child.pid
                    form.running = True
                    print('Child pid is {}'.format(child.pid))
                
                conn = create_connection()
                conn.ensure_connection()
                form.save()
                conn.close()
                sys.stdout.flush()
                # client.close()
                
                return HttpResponseRedirect('/application/AWSsetup')
            
            client.close()
            return render(request, "aws_setup_complete.html")
    else:
        form = awsFiles()

    context = {
        'form': form,
    }
    return render(request, "aws.html", context)

# run AWS setup on the EC2 instance specified by user
def run_aws_setup(request, GPU_instance):
    time.sleep(3)
    text_obj = request.session['text_obj']
    # uploadedAWSPemFile = awsFile.objects.filter(user = request.user).latest('id')
    private_key_file = io.StringIO(text_obj)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    privkey = paramiko.RSAKey.from_private_key(private_key_file)
    try:
        client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey, timeout=10)
    except:
        sys.stdout.flush()
        messages.error(request, 'Error - looks like your AWS remote server is down, please check your instance in the AWS console and connect again')
        return HttpResponseRedirect('/application/aws')

    dir_path = os.path.dirname(os.path.abspath(__file__))
    #tc file
    source_setup = dir_path + '/scripts/setup_aws.sh'
    source_setup = str(source_setup)

    source_fullmonte = dir_path + '/scripts/FullMonteSW_setup.sh'
    source_fullmonte = str(source_fullmonte)

    source_paraview = dir_path + '/scripts/ParaView_setup.sh'
    source_paraview = str(source_paraview)

    source_pdt_space = dir_path + '/scripts/pdt_space_setup.sh'
    source_pdt_space = str(source_pdt_space)

    source_CUDA = dir_path + '/scripts/CUDA_setup.sh'
    source_CUDA = str(source_CUDA)

    source_license = dir_path + '/license/mosek.lic'
    source_license = str(source_license)

    sftp = client.open_sftp()
    client.exec_command('mkdir -p docker_sims')
    client.exec_command('mkdir -p docker_pdt')
    sftp.put(source_setup, 'docker_sims/setup_aws.sh')
    sftp.put(source_fullmonte, 'docker_sims/FullMonteSW_setup.sh')
    sftp.put(source_paraview, 'docker_sims/ParaView_setup.sh')
    sftp.put(source_pdt_space, 'docker_pdt/pdt_space_setup.sh')
    if GPU_instance:
        sftp.put(source_CUDA, 'docker_sims/CUDA_setup.sh')
    # sftp.put(source_license, 'docker_pdt/mosek.lic')
    sftp.chmod('docker_sims/setup_aws.sh', 700)
    sftp.chmod('docker_sims/FullMonteSW_setup.sh', 700)
    sftp.chmod('docker_sims/ParaView_setup.sh', 700)
    sftp.chmod('docker_pdt/pdt_space_setup.sh', 700)
    if GPU_instance:
        sftp.chmod('docker_sims/CUDA_setup.sh', 700)

    # create dummy script to run
    sftp.chdir('docker_sims/')
    file=sftp.file('docker.sh', "w")
    file.write('#!/bin/bash\n')
    file.flush()
    sftp.chmod('docker.sh', 700)

    command = "sudo ~/docker_sims/setup_aws.sh"
    stdin, stdout, stderr = client.exec_command(command)
    stdout_line = stdout.readlines()
    stderr_line = stderr.readlines()
    for line in stdout_line:
        print (line)
    for line in stderr_line:
        print (line)

    command = "sudo ~/docker_sims/FullMonteSW_setup.sh"
    stdin, stdout, stderr = client.exec_command(command)
    stdout_line = stdout.readlines()
    stderr_line = stderr.readlines()
    for line in stdout_line:
        print (line)
    for line in stderr_line:
        print (line)

    command = "sudo ~/docker_sims/ParaView_setup.sh"
    stdin, stdout, stderr = client.exec_command(command)
    stdout_line = stdout.readlines()
    stderr_line = stderr.readlines()
    for line in stdout_line:
        print (line)
    for line in stderr_line:
        print (line)
    
    #pdt-space
    print('start setup pdt-space')
    file=sftp.file('../docker_pdt/docker.sh', "w")
    file.write('#!/bin/bash\n')
    file.write('echo dockertest')
    file.flush()
    sftp.chmod('../docker_pdt/docker.sh', 700)
    command = "sudo sh ~/docker_pdt/pdt_space_setup.sh \"ls /usr/local/pdt-space/data\" 0"
    stdin, stdout, stderr = client.exec_command(command)
    stdout_line = stdout.readlines()
    stderr_line = stderr.readlines()
    for line in stdout_line:
        print (line)
    for line in stderr_line:
        print (line)
    print('end setup pdt-space')

    if GPU_instance:
        command = "sudo ~/docker_sims/CUDA_setup.sh > ~/CUDA_setup.log"
        stdin, stdout, stderr = client.exec_command(command)
        stdout_line = stdout.readlines()
        stderr_line = stderr.readlines()
        for line in stdout_line:
            print (line)
        for line in stderr_line:
            print (line)

    # alias = 'manual'
    conn = create_connection()
    conn.ensure_connection()
    running_process = processRunning.objects.filter(user = request.user).latest('id')
    running_process.running = False
    running_process.save()
    conn.close()
    print('finished')
    client.close()
    sys.stdout.flush()

# AWS setup progress page
def AWSsetup(request):
    running_process = processRunning.objects.filter(user = request.user).latest('id')
    pid = running_process.pid
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    text_obj = request.session['text_obj']
    private_key_file = io.StringIO(text_obj)
    privkey = paramiko.RSAKey.from_private_key(private_key_file)
    try:
        client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey, timeout=10)
    except:
        sys.stdout.flush()
        messages.error(request, 'Error - looks like your AWS remote server is down, please check your instance in the AWS console and connect again')
        return HttpResponseRedirect('/application/aws')

    if running_process.running:
        
        print("get current progress")
        sys.stdout.flush()
        stdin, stdout, stderr = client.exec_command('head -1 ~/setup.log')
        stdout_line = stdout.readlines()
        progress = ''
        if len(stdout_line) > 0:
            progress = stdout_line[0].split()[0]
        else:
            progress = '0.00'
        
        client.close()
        print("set up progress: " + progress)
        print("end current progress")
        sys.stdout.flush()
        progress = (float(progress) * 6)
        start_time = running_process.start_time
        current_time = datetime.now(timezone.utc)
        time_diff = current_time - start_time
        running_time = str(time_diff)
        running_time = running_time.split('.')[0]
        return render(request, "AWSsetup.html", {'progress':progress, 'time':running_time})
    else:
        stdin, stdout, stderr = client.exec_command('rm -rf ~/setup.log')
        client.close()
        return render(request, "aws_setup_complete.html")
    
# parse lines in file
def handle_uploaded_file(f):
    for line in f:
        print (line)

# execute FullMonte simulation
# def exec_simulate(request, channel, command):
    
#     print("start running " + command)
#     sys.stdout.flush()
#     channel.exec_command(command)
#     while True:
#         if channel.exit_status_ready():
#             break
#         rl, wl, xl = select.select([channel],[],[],0.0)
#         if len(rl) > 0:
#             print(channel.recv(1024))
#             sys.stdout.flush()
#     print("finish running")
#     sys.stdout.flush()
#     return  HttpResponseRedirect('/application/simulation_fail')

# simulation progress page
def running(request):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    text_obj = request.session['text_obj']
    private_key_file = io.StringIO(text_obj)
    privkey = paramiko.RSAKey.from_private_key(private_key_file)
    try:
        client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey, timeout=10)
    except:
        sys.stdout.flush()
        messages.error(request, 'Error - looks like your AWS remote server is down, please check your instance in the AWS console and connect again')
        return HttpResponseRedirect('/application/aws')

    stdin, stdout, stderr = client.exec_command('sudo sed -e "s/\\r/\\n/g" ~/sim_run.log > ~/cleaned.log')
    stdin, stdout, stderr = client.exec_command('sudo tail -1 ~/cleaned.log')
    stdout_word = stdout.readlines()
    progress = ''
    if len(stdout_word) > 0:
        # print(stdout_word[-1])
        # sys.stdout.flush()
        if stdout_word[-1].split()[0] == "Progress":
            progress = stdout_word[-1].split()[-1]
    if progress == '':
        if request.session['started'] == "false":
            progress = '0.00%'
        else:
            progress = '100.00%'

        print("got progres: "+progress)
        sys.stdout.flush()
    else:
        request.session['started'] = "true"
        print("got progres: "+progress)
        sys.stdout.flush()
    progress = progress[:-2]
    
    start_time = datetime.strptime(request.session['start_time'], '%Y-%m-%d %H:%M:%S.%f%z')
    current_time = datetime.now(timezone.utc)
    time_diff = current_time - start_time
    running_time = str(time_diff)
    running_time = running_time.split('.')[0]
    print("time is : ", running_time)
    sys.stdout.flush()

    stdin, stdout, stderr = client.exec_command('tail -1 ~/cleaned.log')
    stdout_line = stdout.readlines()
    status = ""
    if len(stdout_line) > 0:
        status = stdout_line[0]
        status = "".join(status.split())
    
    print("status:",status)
    sys.stdout.flush()
    client.close()
    if status == "[info]Simulationrunfinished":
        print("tclsh finish")
        sys.stdout.flush()
        return HttpResponseRedirect('/application/simulation_finish')
    else:
        print("tclsh not finished")
        sys.stdout.flush()
        return render(request, "running.html", {'time':running_time, 'progress':progress})

# page for failed simulation
# def simulation_fail(request):
#     return render(request, "simulation_fail.html")

# Response for finished simulation
def simulation_finish(request):
    # display simulation outputs
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    text_obj = request.session['text_obj']
    private_key_file = io.StringIO(text_obj)
    privkey = paramiko.RSAKey.from_private_key(private_key_file)
    try:
        client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey, timeout=10)
    except:
        sys.stdout.flush()
        messages.error(request, 'Error - looks like your AWS remote server is down, please check your instance in the AWS console and connect again')
        return HttpResponseRedirect('/application/aws')

    ftp = client.open_sftp()
    # ftp.chdir('docker_sims/')
    file=ftp.file('sim_run.log', "r")
    output = file.read().decode()
    if '[error]' in output:
        simulation_failed = True
    else:
        simulation_failed = False
    html_string=''
    # add <p> to output string since html does not support '\n'
    for e in output.splitlines():
        if len(e.split()) > 0  and  e.split()[0] != "Progress":
            html_string += e + '<br />'
    print(output)
    sys.stdout.flush()
    stdin, stdout, stderr = client.exec_command('sudo rm -f ~/cleaned.log')
    ftp.close()
    client.close()

    # save output mesh file info
    # using tcl script name to identify as meshes can be reused
    info = meshFileInfo.objects.filter(user = request.user).latest('id')

    # if there is error, clear output mesh file info so user cannot use visualizer; go simulation failed page
    if(simulation_failed):
        info.fileName = ""
        info.dvhFig = ""
        info.save()
        return render(request, "simulation_fail.html", {'output':html_string})
    
    # otherwise, simulation completed
    # save output mesh info
    outputMeshFile = tclScript.objects.filter(user = request.user).latest('id')
    outputMeshFileName = outputMeshFile.script.name
    info.fileName = outputMeshFileName[:-4] + ".out.vtk"
    info.dvhFig = "<p>Dose Volume Histogram not yet generated</p>"
    info.save()
    # save fluence energy unit
    meshUnit = request.session['meshUnit']
    energyUnit = request.session['energyUnit']
    request.session['fluenceEnergyUnit'] = energyUnit + "/" + meshUnit
    # populate history
    connections.close_all()
    p = Process(target=populate_simulation_history, args=(request, ))
    p.start()
    return render(request, "simulation_finish.html", {'output':html_string})

def populate_simulation_history(request):
    conn = create_connection()
    conn.ensure_connection()

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    text_obj = request.session['text_obj']
    private_key_file = io.StringIO(text_obj)
    privkey = paramiko.RSAKey.from_private_key(private_key_file)
    try:
        client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey, timeout=10)
    except:
        sys.stdout.flush()
        messages.error(request, 'Error - looks like your AWS remote server is down, please check your instance in the AWS console and connect again')
        return HttpResponseRedirect('/application/aws')

    ftp = client.open_sftp()
    # store output files to S3, and populate simulation history
    history = simulationHistory()
    history.simulation_type = 'Fullmonte Simulation'
    history.user = request.user
    mesh = tclInput.objects.filter(user = request.user).latest('id')
    history.mesh_file_path = mesh.meshFile
    history.originalMeshFileName = mesh.originalMeshFileName
    history.meshFileID = mesh.meshFileID
    history.tcl_script_path = tclScript.objects.filter(user = request.user).latest('id').script

    mesh_vtk_name = mesh.meshFile.name
    mesh_name = mesh_vtk_name[:-4]
    tcl_name = history.tcl_script_path.name
    output_vtk_name = tcl_name[:-4] + '.out.vtk'
    output_txt_name = tcl_name[:-4] + '.phi_v.txt'

    try:
        output_vtk_file = ftp.file('docker_sims/' + output_vtk_name)
        # default_storage.save(output_vtk_name, output_vtk_file)
        output_txt_file = ftp.file('docker_sims/' + output_txt_name)
        history.output_vtk_path.save(output_vtk_name, output_vtk_file)
        history.output_txt_path.save(output_txt_name, output_txt_file)
        # default_storage.save(output_txt_name, output_txt_file)
    except:
        print("Cannot save history for simulation output because file does not exist")
    
    ftp.close()
    client.close()
    history.save()

    conn.close()

def simulation_history(request):
    # First check if user is logged-in
    if not request.user.is_authenticated:
        return redirect('please_login')

    history = simulationHistory.objects.filter(user=request.user).order_by('-simulation_time') # order by time (most present at top)
    historySize = history.count()
    if historySize > 0:
        return render(request, "simulation_history.html", {'history':history, 'historySize':historySize})
    else:
        return render(request, "simulation_history_empty.html")
    return render(request, "simulation_history.html", {'history':history, 'historySize':historySize})

#               Current unsolved problem in PDT-SPACE
#       1.  When running the docker image for pdt-space, sometimes the image needs to be downloaded and reinstall again.
#           Sometimes it doesn't need to. As a result, the docker image will occupy more disk space.
#  
#       2.  When the the pdt-space run actually finished (the function launch_pdt_space() printed "finished"),
#           the program is stuck in function  pdt_space_running() and the web page is keep loading when auto refreshing the page(freezed).
#           This only happens once and is not able to reproduce.
#       
def pdt_space(request):
    print("in pdt_space")
    sys.stdout.flush()

    # First check if user is logged-in
    if not request.user.is_authenticated:
        return redirect('please_login')

    try:
        dns = request.session['DNS']
        text_obj = request.session['text_obj']
        tcpPort = request.session['tcpPort']
    except:
        messages.error(request, 'Error - please connect to an AWS remote server before trying to simulate')
        return HttpResponseRedirect('/application/aws')
    
    #lanuch job to search all preset mesh and optical files in the PDT-SPACE repo
    print('before')
    current_process = psutil.Process()
    children = current_process.children(recursive=True)
    for child in children:
        print('Child pid is {}'.format(child.pid))
    connections.close_all()
    p = Process(target=search_pdt_space, args=(request, ))
    p.start()
    print('after')
    current_process = psutil.Process()
    children = current_process.children(recursive=True)
    form = processRunning()
    form.user=request.user
    for child in children:
        form.pid = child.pid
        form.running = True
        print('Child pid is {}'.format(child.pid))
    conn = create_connection()
    conn.ensure_connection()
    form.save()
    conn.close()
    sys.stdout.flush()
    return HttpResponseRedirect('/application/pdt_spcae_wait')


def pdt_spcae_wait(request):
    running_process = processRunning.objects.filter(user = request.user).latest('id')
    pid = running_process.pid
    # client = paramiko.SSHClient()
    # client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    # text_obj = request.session['text_obj']
    # private_key_file = io.StringIO(text_obj)
    # privkey = paramiko.RSAKey.from_private_key(private_key_file)
    # client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey)
    if running_process.running:
        print("get current progress")
        sys.stdout.flush()
        return render(request, "pdt_spcae_wait.html")

    else:
        # client.close()
        print("progress done")
        sys.stdout.flush()
        return HttpResponseRedirect('/application/pdt_space_license')


def search_pdt_space(request):
    time.sleep(3)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    text_obj = request.session['text_obj']
    private_key_file = io.StringIO(text_obj)
    privkey = paramiko.RSAKey.from_private_key(private_key_file)
    try:
        client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey, timeout=10)
    except:
        sys.stdout.flush()
        messages.error(request, 'Error - looks like your AWS remote server is down, please check your instance in the AWS console and connect again')
        return HttpResponseRedirect('/application/aws')

    foo_list = []
    addr_list = []
    command = "sudo sh ~/docker_pdt/pdt_space_setup.sh \"find /usr/local/pdt-space/data -name *.opt\" 0"
    stdin, stdout, stderr = client.exec_command(command)
    stdout_line = stdout.readlines()
    stderr_line = stderr.readlines()
    
    for line in stdout_line:
        if line.rstrip().endswith('.opt'):
            request.session[line.rstrip().split('/')[-1]] = line.rstrip()
            foo_list.append(line.rstrip().split('/')[-1])
            addr_list.append(line.rstrip())
            # print (line)
    opt_list = ",".join(foo_list)
    opt_addr = ",".join(addr_list)
    request.session['opt_list'] = opt_list
    print(request.session['opt_list'])
    print("This is all preset .opt files")
    sys.stdout.flush()
    
    foo_list = []
    addr_list = []
    command = "sudo sh ~/docker_pdt/pdt_space_setup.sh \"find /usr/local/pdt-space/data -name *.mesh\" 0"
    stdin, stdout, stderr = client.exec_command(command)
    stdout_line = stdout.readlines()
    stderr_line = stderr.readlines()
    
    for line in stdout_line:
        if line.rstrip().endswith('.mesh'):
            request.session[line.rstrip().split('/')[-1]] = line.rstrip()
            foo_list.append(line.rstrip().split('/')[-1])
            addr_list.append(line.rstrip())
            # print (line)
    mesh_list = ",".join(foo_list)
    mesh_addr = ",".join(addr_list)
    request.session['mesh_list'] = mesh_list
    print(request.session['mesh_list'])
    print("This is all preset .mesh files")
    
    conn = create_connection()
    conn.ensure_connection()
    running_process = processRunning.objects.filter(user = request.user).latest('id')
    running_process.running = False
    running_process.save()
    conn.close()

    form = pdtPresetData()
    form.user=request.user
    form.opt_list = opt_list
    form.mesh_list = mesh_list

    form.opt_addr = opt_addr
    form.mesh_addr = mesh_addr
    conn = create_connection()
    conn.ensure_connection()
    form.save()
    conn.close()
    print('finished')
    client.close()
    sys.stdout.flush()



def pdt_space_license(request):
    if request.method == 'POST':
        form = mosekLicense(request.POST, request.FILES)
        if form.is_valid():
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            text_obj = request.session['text_obj']
            private_key_file = io.StringIO(text_obj)
            privkey = paramiko.RSAKey.from_private_key(private_key_file)
            try:
                client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey, timeout=10)
            except:
                sys.stdout.flush()
                messages.error(request, 'Error - looks like your AWS remote server is down, please check your instance in the AWS console and connect again')
                return HttpResponseRedirect('/application/aws')
            sftp = client.open_sftp()
            sftp.putfo(request.FILES['mosek_license'], 'docker_pdt/mosek.lic')
            sftp.close()
            client.close()
        else:
            print("pdt_space_license not valid")
        return HttpResponseRedirect('/application/pdt_space_material') 
    else:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        text_obj = request.session['text_obj']
        private_key_file = io.StringIO(text_obj)
        privkey = paramiko.RSAKey.from_private_key(private_key_file)
        try:
            client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey, timeout=10)
        except:
            sys.stdout.flush()
            messages.error(request, 'Error - looks like your AWS remote server is down, please check your instance in the AWS console and connect again')
            return HttpResponseRedirect('/application/aws')
        sftp = client.open_sftp()
        try:
            sftp.stat('docker_pdt/mosek.lic')
        except IOError:
            form = mosekLicense()
            context = {
                'form': form,
            }
            sftp.close()
            client.close()
            return render(request, "pdt_space_license.html", context)
        sftp.close()
        client.close()
        return HttpResponseRedirect('/application/pdt_space_material') 


def pdt_space_material(request):
    print("in pdt_space_material")
    sys.stdout.flush()

    conn = create_connection()
    conn.ensure_connection()
    pdt_info = pdtPresetData.objects.filter(user = request.user).latest('id')
    opt_str = pdt_info.opt_list
    mesh_str = pdt_info.mesh_list

    opt_addr = pdt_info.opt_addr
    mesh_addr = pdt_info.mesh_addr
    # opt_str = request.session['opt_list']
    # mesh_str = request.session['mesh_list']

    print("the opt str is ",opt_str)
    print("the mesh str is ",mesh_str)

    print("the opt addr is ",opt_addr)
    print("the mesh addr is ",mesh_addr)
    conn.close()
    sys.stdout.flush()
    # sys.stdout.flush()

    _opt_list=[]
    _mesh_list=[]
    for sinlge in opt_str.split(','):
        _opt_list.append(sinlge)
    
    for sinlge in mesh_str.split(','):
        _mesh_list.append(sinlge)
        
    if request.method == 'POST':
        
        form = pdtForm(opt_list =_opt_list, mesh_list=_mesh_list, data = request.POST)
        if form.is_valid():
            print(form.cleaned_data)
            op_file = opFileInput()
            op_file.user=request.user
            op_file.total_energy = form.cleaned_data['total_energy']
            op_file.num_packets = form.cleaned_data['num_packets']
            op_file.wave_length = form.cleaned_data['wave_length']
            
            op_file.tumor_weight = form.cleaned_data['tumor_weight']
            
            mesh_name = form.cleaned_data['mesh']
            opt_name = form.cleaned_data['opt']
            op_file.data_name = mesh_name.split('.')[0]
            # op_file.placement_file = request.FILES['light_placement_file']
            # print("light file name is ", op_file.placement_file.name)
            # sys.stdout.flush()
            for sub in mesh_addr.split(','):
                if sub.split('/')[-1] == mesh_name:
                    op_file.data_dir = "/".join(sub.split('/')[:-1])

            for sub in opt_addr.split(','):
                if sub.split('/')[-1] == opt_name:
                    op_file.opt_file = sub

            conn = create_connection()
            conn.ensure_connection()
            op_file.save()
            conn.close()
        else:
            print(" pdt_space_material form not valid")
            print(form.errors)
        sys.stdout.flush()
        return HttpResponseRedirect('/application/pdt_space_lightsource')
    else:
        
        form = pdtForm(opt_list=_opt_list, mesh_list=_mesh_list)
    context = {
        'form': form,
    }
    return render(request, "pdt_space_material.html", context)


def pdt_space_lightsource(request):
    print("in pdt_space_lightsource")

    if request.method == 'POST':
        form = pdtPlaceFile(request.POST, request.FILES)
        if form.is_valid():
            conn = create_connection()
            conn.ensure_connection()
            opfile = opFileInput.objects.filter(user = request.user).latest('id')
            opfile.placement_file = request.FILES['light_placement_file']
            opfile.source_type = form.cleaned_data['source_type']
            opfile.placement_type = form.cleaned_data['placement_type']
            
            opfile.save()
            opfile.light_source_file = "/sims/" + str(opfile.placement_file.name)

            print("check data")
            print(opfile.total_energy)
            print(opfile.num_packets)
            print(opfile.wave_length)
            print(opfile.data_dir)
            print(opfile.data_name)
            print(opfile.source_type)
            print(opfile.tumor_weight)
            print(opfile.placement_type)
            print(opfile.opt_file)
            print(opfile.light_source_file)
            print(opfile.placement_file.name)
            sys.stdout.flush()

            #generate .op file
            dir_path = os.path.dirname(os.path.abspath(__file__))
            source = dir_path + '/pdtOp/pdt_space.op'

            #start by wiping script template
            with open(source, 'r') as f:
                lines = f.readlines()
            
            f = open(source, 'w')
            for line in lines[::-1]:
                del lines[-1]
            
            f = open(source, 'a')
            f.write('RUN_TESTS = false\n\n')
            f.write('TOTAL_ENERGY = ' + opfile.total_energy + '\n\n')
            f.write('NUM_PACKETS = ' + opfile.num_packets + '\n\n')
            f.write('WAVELENGTH = ' + opfile.wave_length + '\n\n')
            f.write('DATA_DIR = ' + opfile.data_dir + '\n\n')
            f.write('DATA_NAME = ' + opfile.data_name + '\n\n')
            f.write('READ_VTK = false\n\n')
            f.write('source_type = ' + opfile.source_type + '\n\n')
            f.write('tumor_weight = ' + opfile.tumor_weight + '\n\n')
            f.write('PLACEMENT_TYPE = ' + opfile.placement_type + '\n\n')
            f.write('TAILORED = false\n\n')
            f.write('OPTICAL_FILE = ' + opfile.opt_file + '\n\n')
            f.write('INIT_PLACEMENT_FILE = ' + opfile.light_source_file + '\n\n')
            f.close()
            f = open(source, 'r')
            ##transfer files
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            text_obj = request.session['text_obj']
            private_key_file = io.StringIO(text_obj)
            privkey = paramiko.RSAKey.from_private_key(private_key_file)
            try:
                client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey, timeout=10)
            except:
                sys.stdout.flush()
                messages.error(request, 'Error - looks like your AWS remote server is down, please check your instance in the AWS console and connect again')
                return HttpResponseRedirect('/application/aws')
            ftp = client.open_sftp()
            ftp.chdir('docker_pdt/')
            file=ftp.file('docker.sh', "w")
            file.write('#!/bin/bash\nexport MOSEKLM_LICENSE_FILE=/sims/mosek.lic\ncd sims/\npdt_space pdt_space.op')
            file.flush()
            ftp.chmod('docker.sh', 700)
            ftp.putfo(f,'./pdt_space.op')
            ftp.putfo(opfile.placement_file, './'+opfile.placement_file.name)
            ftp.close()
            conn.close()
            f.close()

            # lanuch pdt-space with a use process
            print('before')
            current_process = psutil.Process()
            children = current_process.children(recursive=True)
            for child in children:
                print('Child pid is {}'.format(child.pid))
            connections.close_all()
            p = Process(target=launch_pdt_space, args=(request, ))
            p.start()
            print('after')
            current_process = psutil.Process()
            children = current_process.children(recursive=True)
            form = processRunning()
            form.user=request.user
            for child in children:
                form.pid = child.pid
                form.running = True
                print('Child pid is {}'.format(child.pid))
            conn = create_connection()
            conn.ensure_connection()
            form.save()
            conn.close()
            sys.stdout.flush()
            # channel = client.get_transport().open_session()
            # command = "sudo sh ~/docker_pdt/pdt_space_setup.sh \"sh /sims/docker.sh\" 1 "
            # stdin, stdout, stderr = client.exec_command(command)
            # client.exec_command(command)
            # stdout_line = stdout.readlines()
            # stderr_line = stderr.readlines()
            # for line in stdout_line:
            #     print (line)
            # for line in stderr_line:
            #     print (line)
            # sys.stdout.flush()  
            # time.sleep(3)
            client.close()
            return HttpResponseRedirect('/application/pdt_space_running')
        else:
            print(form.errors)
            sys.stdout.flush()
            return HttpResponseRedirect('/application/pdt_space_running')
    else:
        form = pdtPlaceFile()
        context = {
            'form': form,
        }


    return render(request, "pdt_space_lightsource.html", context)

def pdt_space_running(request):
    # print("checking the pdt-space status")
    sys.stdout.flush()
    running_process = processRunning.objects.filter(user = request.user).latest('id')
    pid = running_process.pid
    # print("got running process")
    sys.stdout.flush()
    if running_process.running:
        print("pdt-space running")
        sys.stdout.flush()
        return render(request, "pdt_space_running.html")

    else:
        # client.close()
        print("pdt-space done")
        sys.stdout.flush()
        return HttpResponseRedirect('/application/pdt_space_finish')
    # client = paramiko.SSHClient()
    # client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    # text_obj = request.session['text_obj']
    # private_key_file = io.StringIO(text_obj)
    # privkey = paramiko.RSAKey.from_private_key(private_key_file)
    # client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey)

    # # stdin, stdout, stderr = client.exec_command('sudo sed -e "s/\\r/\\n/g" ~/pdt_run.log > ~/cleaned_pdt_run.log')
    # stdin, stdout, stderr = client.exec_command('tail -1 ~/eval_result.log')
    # stdout_line = stdout.readlines()
    # status = ""

    # if len(stdout_line) > 0:
    #     status = stdout_line[0]
    #     status = "".join(status.split())
    
    # print("status:",status)
    # sys.stdout.flush()
    # client.close()
    
    # # if status == "[info]PDT-SPACErunfinished":
    # if status == "===========================================================================":
    #     print("pdt space finish")
    #     sys.stdout.flush()
    #     return HttpResponseRedirect('/application/pdt_space_finish')
    # else:
    #     print("pdt space not finished")
    #     sys.stdout.flush()
    #     return render(request, "pdt_space_running.html")

def launch_pdt_space(request):
    time.sleep(3)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    text_obj = request.session['text_obj']
    private_key_file = io.StringIO(text_obj)
    privkey = paramiko.RSAKey.from_private_key(private_key_file)
    try:
        client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey, timeout=10)
    except:
        sys.stdout.flush()
        messages.error(request, 'Error - looks like your AWS remote server is down, please check your instance in the AWS console and connect again')
        return HttpResponseRedirect('/application/aws')

    command = "sudo sh ~/docker_pdt/pdt_space_setup.sh \"sh /sims/docker.sh\" 1 "
    stdin, stdout, stderr = client.exec_command(command)
    stdout_line = stdout.readlines()
    stderr_line = stderr.readlines()
    for line in stdout_line:
        print (line)
    for line in stderr_line:
        print (line)
    sys.stdout.flush() 

    conn = create_connection()
    conn.ensure_connection()
    running_process = processRunning.objects.filter(user = request.user).latest('id')
    running_process.running = False
    running_process.save()
    conn.close()
    client.close()
    print('finished')
    sys.stdout.flush()

def pdt_space_finish(request):
    # print("in fihish")
    sys.stdout.flush()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    text_obj = request.session['text_obj']
    private_key_file = io.StringIO(text_obj)
    privkey = paramiko.RSAKey.from_private_key(private_key_file)
    try:
        client.connect(hostname=request.session['DNS'], username='ubuntu', pkey=privkey, timeout=10)
    except:
        sys.stdout.flush()
        messages.error(request, 'Error - looks like your AWS remote server is down, please check your instance in the AWS console and connect again')
        return HttpResponseRedirect('/application/aws')
    
    # pdt-space output in ~/eval_result.log
    ftp = client.open_sftp()
    file=ftp.file('eval_result.log', "r")
    output = file.read().decode()
    output_lines = output.splitlines()
    # get num_material and num_source by reading the log file
    # num_material: number of materials in input mesh
    # num_source: number of light source placement
    num_material = 0
    num_source = 0
    index = 0
    for line in output_lines:
        if output_lines[index].split()[0] == "Directory":
            num_material = output_lines[index + 1].split()[-1]
            break
        index += 1
    print(num_material)

    index = -1
    for line in output_lines:
        if len(output_lines[index].split()) == 5:
            if output_lines[index].split()[0] == "Number" and output_lines[index].split()[3] == "sources:":
                num_source = output_lines[index].split()[-1]
                break
        index -= 1
    print(num_source)    

    html_fluence_dist=''
    html_pow_alloc=''
    num_material = int(num_material)
    num_source = int(num_source)

    output_info = output_lines[-7:-5]
    time_simu = output_info[0].split()[8]
    time_opt = output_info[1].split()[3]

    output_info = output_lines[-12 - num_source :-12]
    for e in output_info:
        html_pow_alloc += e + '<br />'

    output_info = output_lines[-12 - num_source - 2 - num_material :-12 - num_source - 2]
    for e in output_info:
        html_fluence_dist += e + '<br />'

    sys.stdout.flush()
    ftp.close()
    client.close()
    return render(request, "pdt_space_finish.html", {'html_fluence_dist':html_fluence_dist, 'html_pow_alloc':html_pow_alloc, 'time_simu':time_simu, 'time_opt':time_opt})

def pdt_space_visualize(request):
    """
    Todo:
    In pdt_space_finish, save mesh file info
        info = meshFileInfo.objects.filter(user = request.user).latest('id')
        info.fileName = <output mesh name>
        exists = <check if file actually exists on disk>
        if exists:
            info.remoteFileExists = true
        else
            info.remoteFileExists = false
        info.dvhFig = <dose volume histogram html string generated>
        info.maxFluence = <maximum fluence from simulation>
    In displayVisualization
        add a new parameter to check if visualization request comes from fullmonte or pdtspace.
        p = Process(target=visualizer, args=(outputMeshFileName, fileExists, dns, tcpPort, text_obj, caller))
            where caller = 'fullmonte' or 'pdtspace'
    In visualizer3D.py
        choose file path depending on caller
        if caller == 'fullmonte':
            cmd = "Visualizer --paraview /home/ubuntu/ParaView-5.8.1-osmesa-MPI-Linux-Python2.7-64bit/ --data /home/ubuntu/docker_sims/ --port " + tcpPort
        else:
            cmd = "Visualizer --paraview /home/ubuntu/ParaView-5.8.1-osmesa-MPI-Linux-Python2.7-64bit/ --data /home/ubuntu/docker_pdt/ --port " + tcpPort
    """
    return HttpResponseRedirect('/application/displayVisualization')