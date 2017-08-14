import numpy as np
import ctypes as ct
import cv2
import sys
import argparse
from datasets import ViewDataSet3D
from completion import CompletionNet
import torch
from torchvision import datasets, transforms
from torch.autograd import Variable
import time
#import matplotlib
#matplotlib.use('GTKAgg')
import matplotlib.pyplot as plt
from numpy import cos, sin

import utils


showsz = 256
mousex,mousey=0.5,0.5
changed=True
pitch,yaw,x,y,z = 0,0,0,0,0
roll = 0
org_pitch, org_yaw, org_x, org_y, org_z = 0,0,0,0,0
org_roll = 0
mousedown = False
clickstart = (0,0)
fps = 0

dll=np.ctypeslib.load_library('render','.')


def onmouse(*args):
    global mousex,mousey,changed
    global pitch,yaw,x,y,z
    global org_pitch, org_yaw, org_x, org_y, org_z
    global org_roll, roll
    global clickstart

    if args[0] == cv2.EVENT_LBUTTONDOWN:
        org_pitch, org_yaw, org_x, org_y, org_z =\
        pitch,yaw,x,y,z
        clickstart = (mousex, mousey)

    if args[0] == cv2.EVENT_RBUTTONDOWN:
        org_roll = roll
        clickstart = (mousex, mousey)

    if (args[3] & cv2.EVENT_FLAG_LBUTTON):
        pitch = org_pitch - (mousex - clickstart[0])/10
        yaw = org_yaw - (mousey - clickstart[1])
        changed=True

    if (args[3] & cv2.EVENT_FLAG_RBUTTON):
        roll = org_roll - (mousex - clickstart[0])/50
        changed=True

    my=args[1]
    mx=args[2]
    mousex=mx/float(showsz)
    mousey=my/float(showsz * 2)



def showpoints(img, depth, model, rts):
    global mousex,mousey,changed
    global pitch,yaw,x,y,z,roll
    global fps
    show=np.zeros((showsz,showsz * 2,3),dtype='uint8')
    minimap=np.zeros((showsz,showsz,3),dtype='uint8')
    target_depth = np.zeros((showsz,showsz * 2)).astype(np.float32)
    overlay = False
    show_depth = False
    showmin = True
    cv2.namedWindow('show3d')
    cv2.moveWindow('show3d',0,0)
    cv2.setMouseCallback('show3d',onmouse)
    cv2.namedWindow('minimap')
    cv2.moveWindow('minimap',showsz*3,0)
    
    xs = [item[0,-1] for item in  rts]
    ys = [item[1,-1] for item in  rts]

    imgv = Variable(torch.zeros(1,3, 256, 512)).cuda()
    maskv = Variable(torch.zeros(1,1, 256, 512)).cuda()

    cpose = np.eye(4)
    
    def render(img, depth, pose, model):
        global fps
        t0 = time.time()
        dll.render(ct.c_int(img.shape[0]),
                   ct.c_int(img.shape[1]),
                   img.ctypes.data_as(ct.c_void_p),
                   depth.ctypes.data_as(ct.c_void_p),
                   pose.ctypes.data_as(ct.c_void_p),
                   show.ctypes.data_as(ct.c_void_p),
                   target_depth.ctypes.data_as(ct.c_void_p)
                  )
        if model:
            tf = transforms.ToTensor()
            source = tf(show)
            source_depth = tf(np.expand_dims(target_depth, 2))
            #print(source.size(), source_depth.size())

            imgv.data.copy_(source)
            maskv.data.copy_(source_depth)

            recon = model(imgv, maskv)
            #print(recon.size())
            show2 = recon.data.cpu().numpy()[0].transpose(1,2,0)
            show[:] = (show2[:] * 255).astype(np.uint8)

        t1 = time.time()
        t = t1-t0
        fps = 1/t
        
        minimap[:] = 0
        xs = [item[0,-1] for item in  rts]
        ys = [item[1,-1] for item in  rts]
        
        maxx = np.max(xs)
        minx = np.min(xs)
        maxy = np.max(ys)
        miny = np.min(ys)
        
        for i in range(len(xs)):
            cv2.circle(minimap,(int((xs[i] - minx) * showsz / (maxx - minx)),int((ys[i] - miny) * showsz / (maxy - miny))), 5, (0,0,255), -1)

        cv2.circle(minimap,(int((x - minx) * showsz / (maxx - minx)),int((y - miny) * showsz / (maxy - miny))), 5, (0,255,255), -1)
        cv2.waitKey(5)%256
        

    while True:

        if changed:            
            
            current_t = np.eye(4)
            current_t[0,-1] = x
            current_t[1,-1] = y
            current_t[2,-1] = z       
            alpha = yaw
            beta = pitch
            gamma = roll
            cpose = np.zeros(16) 
            cpose[0] = cos(alpha) * cos(beta);
            cpose[1] = cos(alpha) * sin(beta) * sin(gamma) - sin(alpha) * cos(gamma);
            cpose[2] = cos(alpha) * sin(beta) * cos(gamma) + sin(alpha) * sin(gamma);
            cpose[3] = 0
            
            cpose[4] = sin(alpha) * cos(beta);
            cpose[5] = sin(alpha) * sin(beta) * sin(gamma) + cos(alpha) * cos(gamma);
            cpose[6] = sin(alpha) * sin(beta) * cos(gamma) - cos(alpha) * sin(gamma);
            cpose[7] = 0
            cpose[8] = -sin(beta);
            cpose[9] = cos(beta) * sin(gamma);
            cpose[10] = cos(beta) * cos(gamma);
            cpose[11] = 0
            cpose[12:16] = 0
            cpose[15] = 1           
            cpose = cpose.reshape((4,4))      
            cpose = np.dot(cpose, current_t)
            current_rt = cpose
            rotation = np.array([[0,-1,0,0],[-1,0,0,0],[0,0,1,0],[0,0,0,1]])
            current_rt = np.dot(rotation, current_rt)
            
            dist = []
            for i in range(len(rts)):
                rt = rts[i]
                relative = np.dot(current_rt, np.linalg.inv(rt))
                dist.append( np.sqrt(np.sum(relative[0:3, -1] **2)))
            
            if showmin:
                idx = np.argsort(dist)[0]
            else:
                idx = np.argsort(dist)[1]
            print(dist)
            img = sources[idx]
            depth = source_depths[idx]
            rt = rts[idx]
            relative = np.dot(current_rt, np.linalg.inv(rt))
            
            print(idx)
            print(relative)
            
            
            
            render(img, depth, relative.astype(np.float32), model)
            
            changed = False
        
        if overlay:
            min_idx = np.argsort(dist)[0]
            target = sources[min_idx]
            show_out = (show/2 + target/2).astype(np.uint8)
        elif show_depth:
            show_out = (target_depth * 10).astype(np.uint8)
        else:
            show_out = show

        cv2.putText(show,'pitch %.3f yaw %.2f roll %.3f x %.2f y %.2f z %.2f'%(pitch, yaw, roll, x, y, z),(15,showsz-15),0,0.5,cv2.cv.CV_RGB(255,255,255))
        cv2.putText(show,'fps %.1f'%(fps),(15,15),0,0.5,cv2.cv.CV_RGB(255,255,255))

        show_rgb = cv2.cvtColor(show_out, cv2.COLOR_BGR2RGB)
        cv2.imshow('show3d',show_rgb)
        cv2.imshow('minimap',minimap)
        
        cmd=cv2.waitKey(5)%256

        if cmd==ord('q'):
            break

        elif cmd == ord('w'):
            x += 0.05
            changed = True
        elif cmd == ord('s'):
            x -= 0.05
            changed = True
        elif cmd == ord('a'):
            y -= 0.05
            changed = True
        elif cmd == ord('d'):
            y += 0.05
            changed = True
        elif cmd == ord('z'):
            z += 0.02
            changed = True
        elif cmd == ord('x'):
            z -= 0.02    
            changed = True

        elif cmd == ord('r'):
            pitch,yaw,x,y,z = 0,0,0,0,0
            roll = 0
            changed = True
        
            
        elif cmd == ord('n'):
            dist = []
            
            for i in range(len(rts)):
                rt = rts[i]
                relative = np.dot(current_rt, np.linalg.inv(rt))
                dist.append( np.sqrt(np.sum(relative[0:3, -1] **2)))
            
            idx = np.argmin(dist)
            
            print(idx)
            
            RT = rts[idx].reshape((4,4))
            rotation = np.array([[0,-1,0,0],[-1,0,0,0],[0,0,1,0],[0,0,0,1]])
            RT = np.dot(np.linalg.inv(rotation), RT)
            
            R = RT[:3,:3]
            T = RT[:3,-1]
                        
            x,y,z = np.dot(np.linalg.inv(R),T)
            roll, pitch, yaw = (utils.rotationMatrixToEulerAngles(R))
            
            changed = True            
        elif cmd == ord('m'):
            showmin = not showmin
            changed = True
            
        elif cmd == ord('o'):
            overlay = not overlay



def show_target(target_img):
    cv2.namedWindow('target')
    cv2.moveWindow('target',0,256 + 50)
    show_rgb = cv2.cvtColor(target_img, cv2.COLOR_BGR2RGB)

    cv2.imshow('target', show_rgb)

if __name__=='__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--debug'  , action='store_true', help='debug mode')
    parser.add_argument('--dataroot'  , required = True, help='dataset path')
    parser.add_argument('--idx'  , type = int, default = 0, help='index of data')
    parser.add_argument('--model'  , type = str, default = '', help='path of model')
    opt = parser.parse_args()
    d = ViewDataSet3D(root=opt.dataroot, transform = np.array, mist_transform = np.array, seqlen = 2, off_3d = False)

    model = None
    if opt.model != '':
        comp = CompletionNet()
        comp = torch.nn.DataParallel(comp).cuda()
        comp.load_state_dict(torch.load(opt.model))
        model = comp.module
        model.eval()
    print(model)
    
    idx = opt.idx
    uuids, rts = d.get_scene_info(idx)
    
    sources = []
    source_depths = []
    poses = []
    
    for k,v in uuids:
        print(v)
        data = d[v]
        source = data[0][0]
        source_depth = data[2][0]
        
        sources.append(source)
        source_depths.append(source_depth)
    
    showpoints(sources, source_depths, model, rts)