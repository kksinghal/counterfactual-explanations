import argparse
import random
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.optim.lr_scheduler import ExponentialLR
from torchvision import datasets, transforms
from torch import autograd
from torch.autograd import Variable
import torch.multiprocessing as mp
import model

import signal
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import os
#import logutil
import time
from math import log2
from atari_data import MultiEnvironment, ablate_screen
#from env_test import test_game

os.environ['OMP_NUM_THREADS'] = '1'

from scipy.misc import imsave

from collections import deque

#ts = logutil.TimeSeries('Atari Distentangled Auto-Encoder')


print('Parsing arguments')
parser = argparse.ArgumentParser()
parser.add_argument('--batch_size', type=int, default=64)
parser.add_argument('--epsilon', type=float, default=.2)
parser.add_argument('--ae_lr', type=float, default=.0001)
parser.add_argument('--checkpoint_dir', type=str, default='')
parser.add_argument('--latent', type=int, default=16)
parser.add_argument('--agent_latent', type=int, default=32)
parser.add_argument('--env', type=str, default='SpaceInvaders-v0')
parser.add_argument('--agent_file', type=str, default="spaceinvaders-v0-bottom-7fskip_latent32/model.160.tar")
parser.add_argument('--missing', type=str, default="none")
parser.add_argument('--info', type=str, default="")
parser.add_argument('--m_frames', type=int, default=15)
parser.add_argument('--fskip', type=int, default=8)
parser.add_argument('--gpu', type=int, default=0)


args = parser.parse_args()

map_loc = {
        'cuda:0': 'cuda:'+str(args.gpu),
        'cuda:1': 'cuda:'+str(args.gpu),
        'cuda:2': 'cuda:'+str(args.gpu),
        'cuda:3': 'cuda:'+str(args.gpu),
        'cuda:4': 'cuda:'+str(args.gpu),
        'cuda:5': 'cuda:'+str(args.gpu),
        'cuda:7': 'cuda:'+str(args.gpu),
        'cuda:6': 'cuda:'+str(args.gpu),
        'cpu': 'cpu',
}

print('Initializing OpenAI environment...')
if args.fskip % 2 == 0 and args.env == 'SpaceInvaders-v0':
    print("SpaceInvaders needs odd frameskip due to bullet alternations")
    args.fskip = args.fskip -1


envs = MultiEnvironment(args.env, args.batch_size, args.fskip)
action_size = envs.get_action_size()

print('Building models...')
torch.cuda.set_device(args.gpu)
if not os.path.isfile(args.agent_file):
    print("need an agent file")
    exit()
    args.agent_file = args.env + ".model.80.tar"
agent = model.Agent(action_size, args.agent_latent).cuda()
agent.load_state_dict(torch.load(args.agent_file, map_location=map_loc))


###################

z_dim = args.latent


Q = model.Q_net(z_dim).cuda() 
P = model.P_net(z_dim).cuda()     # Encoder/Decoder


# Set optimizators
P_decoder = optim.Adam(P.parameters(), lr=args.ae_lr)
Q_encoder = optim.Adam(Q.parameters(), lr=args.ae_lr)

####################



bs = args.batch_size
TINY = 1e-15
variance = 1

def zero_grads():
    P.zero_grad()
    Q.zero_grad()

def imq_kernel(X: torch.Tensor,
               Y: torch.Tensor,
               h_dim: int):
    batch_size = X.size(0)

    p2_norm_x = X.pow(2).sum(1).unsqueeze(0)
    norms_x = X.sum(1).unsqueeze(0)
    prods_x = torch.mm(norms_x, norms_x.t())
    dists_x = p2_norm_x + p2_norm_x.t() - 2 * prods_x

    p2_norm_y = Y.pow(2).sum(1).unsqueeze(0)
    norms_y = X.sum(1).unsqueeze(0)
    prods_y = torch.mm(norms_y, norms_y.t())
    dists_y = p2_norm_y + p2_norm_y.t() - 2 * prods_y

    dot_prd = torch.mm(norms_x, norms_y.t())
    dists_c = p2_norm_x + p2_norm_y.t() - 2 * dot_prd

    stats = 0
    for scale in [.1, .2, .5, 1., 2., 5., 10.]:
        C = 2 * h_dim * 1.0 * scale
        res1 = C / (C + dists_x)
        res1 += C / (C + dists_y)

        if torch.cuda.is_available():
            res1 = (1 - torch.eye(batch_size).cuda()) * res1
        else:
            res1 = (1 - torch.eye(batch_size)) * res1

        res1 = res1.sum() / (batch_size - 1)
        res2 = C / (C + dists_c)
        res2 = res2.sum() * 2. / (batch_size)
        stats += res1 - res2
    return stats


# Maximum Mean Discrepancy between z and a reference distribution
# This term goes to zero if z is perfectly normal (with variance sigma**2)
def mmd_normal_penalty(z, sigma=1.0):
    batch_size, latent_dim = z.shape
    z_fake = torch.randn(batch_size, latent_dim).cuda() * sigma
    z_fake = model.norm(z_fake)
    mmd_loss = -imq_kernel(z, z_fake, h_dim=latent_dim)
    return mmd_loss.mean() 


mse = nn.MSELoss(reduction = "elementwise_mean")
#https://blog.paperspace.com/adversarial-autoencoders-with-pytorch/
def autoencoder_step(X):
    z_sample = Q(X)
    X_sample = P(z_sample)
    recon_loss = mse(X_sample + TINY, X + TINY)
   

    return recon_loss



mil = 1000000
def train(epoch):
    new_frame_rgb, new_frame_bw = envs.reset()

    agent_state = Variable(torch.Tensor(ablate_screen(new_frame_bw, args.missing)).cuda())
    agent_state_history = deque([agent_state, agent_state.clone(), agent_state.clone(),agent_state.clone()], maxlen=4)
    
    actions_size = envs.get_action_size()
    
    fs = 0
    for i in range(int( mil / bs)):
        agent_state = torch.cat(list(agent_state_history), dim=1)#torch.cat(list(agent_state_history), dim=1)

        z_a = agent(agent_state).detach()
        p = F.softmax(agent.pi(z_a), dim=1)

        
        #loss functions
        ae_loss =  autoencoder_step(z_a)
        mmd_loss = mmd_normal_penalty(Q(z_a)) * 32

        (ae_loss + mmd_loss).backward()

        P_decoder.step()
        Q_encoder.step()
        zero_grads()


        if np.random.random_sample() < args.epsilon:
            actions = np.random.randint(actions_size, size=bs)
        else:
            actions = p.max(1)[1].data.cpu().numpy()

        new_frame_rgb, new_frame_bw, _, done, _ = envs.step(actions)

        agent_state_history.append(Variable(torch.Tensor(ablate_screen(new_frame_bw, args.missing)).cuda()))
        
        if np.sum(done) > 0:
            for j, d in enumerate(done):
                if d:
                    agent_state_history[0][j] =  agent_state_history[3][j].clone()
                    agent_state_history[1][j] =  agent_state_history[3][j].clone()
                    agent_state_history[2][j] =  agent_state_history[3][j].clone()
        
        if i % 20 == 0:
            print("--LOSS-- Recon: {:.4f}, mmd: {:.8f}".format(ae_loss.item(), mmd_loss.item()))
            if i % 300 == 0:
                fs = (i * args.batch_size) + (epoch * mil)
                print("{} frames processed. {:.2f}% complete".format(fs , 100* (fs / (args.m_frames * mil))))
               

def save_models():
    torch.save(Q.state_dict(), os.path.join(args.checkpoint_dir, 'Q'))
    torch.save(P.state_dict(), os.path.join(args.checkpoint_dir, 'P'))
    
def main():
    print('creating directories')
    if args.checkpoint_dir == '':
        args.checkpoint_dir = "normalized_{}_agent{}_latent{}_lr{}_fskip{}_eps{}".format(args.info , args.agent_file, args.latent,args.ae_lr, args.fskip, args.epsilon)
    
    os.makedirs(args.checkpoint_dir , exist_ok=True)
    img_dir = os.path.join(args.checkpoint_dir, "imgs")
    os.makedirs(img_dir, exist_ok=True)

    test_env = MultiEnvironment(args.env, args.batch_size, args.fskip)
    print("getting original scores")
    #original_rewards, _ = test_game(agent, Q, P, test_env, args.missing, use_original_agent = True)

    for i in range(args.m_frames):
        train(i)
        print("saving models to directory: {}". format(args.checkpoint_dir))
        save_models()
        print("Evaluating the Autoencoder")
        test_env = MultiEnvironment(args.env, args.batch_size, args.fskip)

        #total_rewards, total_diffs = test_game(agent, Q, P, test_env, args.missing, use_original_agent = False)
        #print("original score: {:.3f}, std: {:.3f}".format(np.mean(original_rewards),np.std(original_rewards)))
        #print("mean score:     {:.3f}, std: {:.3f}".format(np.mean(total_rewards),   np.std(total_rewards)))
        #print("mean action diff probablity: {:.3f}, std: {:.3f}".format(np.mean(total_diffs),np.std(total_diffs)))


if __name__ == '__main__':
    main()

