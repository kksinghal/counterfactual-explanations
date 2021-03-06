# DCGAN-like generator and discriminator
import numpy as np
import torch
from torch import nn
import torch.nn.functional as F
from torch.autograd import Variable
from torch.nn import init
import torch.nn.init as weight_init

channels = 3


def normalize_vector(x, eps=.0001):
    # Add epsilon for numerical stability when x == 0
    norm = torch.norm(x, p=2, dim=1) + eps
    return x / norm.expand(1, -1).t()


class Encoder(nn.Module):
    def __init__(self, latent_size):
        super(Encoder, self).__init__()
        self.latent_size = latent_size
        self.leaky = nn.LeakyReLU(0.2, inplace=True)

        self.conv0 = (nn.Conv2d(channels, 32, 3, stride=2, padding=(1, 1)))
        self.batch0 = nn.BatchNorm2d(32)
        # Input: 40x40x?
        self.conv1 = nn.Conv2d(32, 64, 3, stride=2, padding=(1, 1))
        self.batch1 = nn.BatchNorm2d(64)
        # 40 x 40 x 64
        self.conv2 = nn.Conv2d(64, 128, 4, stride=2, padding=(1, 1))
        self.batch2 = nn.BatchNorm2d(128)
        # 20 x 20 x 128
        self.conv3 = nn.Conv2d(128, 256, 4, stride=2, padding=(1, 1))
        self.batch3 = nn.BatchNorm2d(256)
        # 10 x 10 x 256
        self.conv4 = nn.Conv2d(256, 256, 4, stride=2, padding=(1, 1))
        self.batch4 = nn.BatchNorm2d(256)
        # 5 x 5 x 256
        self.conv5 = nn.Conv2d(256, 256, 3, stride=1, padding=(0, 0))
        self.batch5 = nn.BatchNorm2d(256)
        # 3 x 3 x 256

        self.hidden_units = 3 * 3 * 256
        self.fc = nn.Linear(self.hidden_units, latent_size)

    def forward(self, x):
        # (hx, cx) = memory
        x = self.leaky(self.batch0(self.conv0(x)))
        x = self.leaky(self.batch1(self.conv1(x)))
        x = self.leaky(self.batch2(self.conv2(x)))
        x = self.leaky(self.batch3(self.conv3(x)))
        x = self.leaky(self.batch4(self.conv4(x)))
        x = self.leaky(self.batch5(self.conv5(x)))
        # x = x.view((-1, self.hidden_units))
        x = x.reshape((-1, self.hidden_units))

        return self.fc(x)


def catv(x, y):
    bs = x.size(0)
    y = y.unsqueeze(2).unsqueeze(3)
    size_x = x.size(2)
    size_y = x.size(3)

    v_to_cat = y.expand(bs, y[0].size(0), size_x, size_y)

    return torch.cat([x, v_to_cat], dim=1)


class Generator(nn.Module):
    def __init__(self, e_s_dim, action_size):
        super(Generator, self).__init__()
        self.e_s_dim = e_s_dim
        use_value = 0
        action_size += use_value

        self.fc = nn.Linear(e_s_dim + action_size, e_s_dim)
        self.deconv1 = nn.ConvTranspose2d(e_s_dim + action_size, 512, 4, stride=2)
        self.batch1 = nn.BatchNorm2d(512)
        self.deconv2 = nn.ConvTranspose2d(512 + action_size, 256, 4, stride=2, padding=0)  # 10
        self.batch2 = nn.BatchNorm2d(256)
        self.deconv3 = nn.ConvTranspose2d(256 + action_size, 128, 4, stride=2, padding=(1, 1))  # 20
        self.batch3 = nn.BatchNorm2d(128)
        self.deconv4 = nn.ConvTranspose2d(128 + action_size, 128, 4, stride=2, padding=(1, 1))  # 40
        self.batch4 = nn.BatchNorm2d(128)
        self.deconv5 = nn.ConvTranspose2d(128 + action_size, 64, 4, stride=2, padding=(1, 1))
        self.batch5 = nn.BatchNorm2d(64)
        self.deconv6 = nn.ConvTranspose2d(64 + action_size, channels, 4, stride=2, padding=(1, 1))

    def forward(self, x, y):
        x = F.relu(self.fc(torch.cat([x, y], dim=1)))
        # x = x.view((-1, self.e_s_dim, 1, 1))
        x = x.reshape((-1, self.e_s_dim, 1, 1))
        x = F.relu(self.batch1(self.deconv1(catv(x, y))))
        x = F.relu(self.batch2(self.deconv2(catv(x, y))))
        x = F.relu(self.batch3(self.deconv3(catv(x, y))))
        x = F.relu(self.batch4(self.deconv4(catv(x, y))))
        x = F.relu(self.batch5(self.deconv5(catv(x, y))))
        x = self.deconv6(catv(x, y))

        return torch.sigmoid(x)


class Discriminator(nn.Module):
    def __init__(self, latent_size, action_size):
        super(Discriminator, self).__init__()

        self.lin1 = nn.Linear(latent_size, latent_size)
        self.lin2 = nn.Linear(latent_size, latent_size)
        self.pi = nn.Linear(latent_size, action_size)
        self.v = nn.Linear(latent_size, 1)

    def forward(self, x):
        x = self.lin1(x)
        x = F.dropout(x, p=0.2, training=self.training)
        x = F.leaky_relu(x)
        x = self.lin2(x)
        x = F.dropout(x, p=0.2, training=self.training)
        x = F.leaky_relu(x)

        return F.softmax(self.pi(x), dim=1), self.v(x)


N = 32


def norm(x):
    norm = torch.norm(x, p=2, dim=1)
    x = x / (norm.expand(1, -1).t() + .0001)
    return x


# TODO: Something wrong with N, it should be 128
#  Encoder
class Q_net(nn.Module):
    def __init__(self, wae_latent):
        super(Q_net, self).__init__()
        self.lin1 = nn.Linear(32, N)
        self.bn1 = nn.BatchNorm1d(N)
        self.lin2 = nn.Linear(N, N)
        self.bn2 = nn.BatchNorm1d(N)
        self.lin3gauss = nn.Linear(N, wae_latent)

    def forward(self, x):
        # x = F.dropout(self.lin1(x), p=0.25, training=self.training)
        x = self.lin1(x)
        x = self.bn1(x)
        x = F.leaky_relu(x)

        x = self.lin2(x)
        x = self.bn2(x)
        x = F.leaky_relu(x)
        # x = F.dropout(self.lin2(x), p=0.25, training=self.training)
        # x = F.relu(x)#leaky(x)
        xgauss = self.lin3gauss(x)
        return norm(xgauss)


# Decoder
class P_net(nn.Module):
    def __init__(self, wae_latent):
        super(P_net, self).__init__()
        self.lin1 = nn.Linear(wae_latent, N)
        self.bn1 = nn.BatchNorm1d(N)
        self.lin2 = nn.Linear(N, N)
        self.bn2 = nn.BatchNorm1d(N)
        self.lin3 = nn.Linear(N, 32)

    def forward(self, x):
        # x = self.lin1(x)
        # x = F.dropout(x, p=0.25, training=self.training)
        # x = F.relu(x)#leaky(x)
        # x = self.lin2(x)
        # x = F.dropout(x, p=0.25, training=self.training)
        # x = F.relu(x)#leaky(x)
        x = self.lin1(x)
        x = self.bn1(x)
        x = F.leaky_relu(x)

        x = self.lin2(x)
        x = self.bn2(x)
        x = F.leaky_relu(x)
        x = self.lin3(x)
        return x


# Not Used
# Discriminator
class D_net_gauss(nn.Module):
    def __init__(self, z_dim):
        super(D_net_gauss, self).__init__()
        self.lin1 = nn.Linear(z_dim, N)
        self.lin2 = nn.Linear(N, N)
        self.lin3 = nn.Linear(N, 1)

    def forward(self, x):
        x = F.dropout(self.lin1(x), p=0.2, training=self.training)
        x = F.relu(x)  # leaky(x)
        x = F.dropout(self.lin2(x), p=0.2, training=self.training)
        x = F.relu(x)  # leaky(x)
        return torch.sigmoid(self.lin3(x))


class Agent(torch.nn.Module):  # an actor-critic neural network
    def __init__(self, num_actions, latent_size=256):
        super(Agent, self).__init__()

        self.latent_size = latent_size
        self.conv1 = nn.Conv2d(4, 32, 3, stride=2, padding=1)
        self.conv2 = nn.Conv2d(32, 32, 3, stride=2, padding=1)
        self.conv3 = nn.Conv2d(32, 32, 3, stride=2, padding=1)
        self.conv4 = nn.Conv2d(32, 32, 3, stride=2, padding=1)
        self.linear = nn.Linear(32 * 5 * 5, self.latent_size)
        self.critic_linear, self.actor_linear = nn.Linear(latent_size, 1), nn.Linear(latent_size, num_actions)

    def get_latent_size(self):
        return self.latent_size

    def forward(self, inputs):
        x = F.elu(self.conv1(inputs))
        x = F.elu(self.conv2(x))
        x = F.elu(self.conv3(x))
        x = F.elu(self.conv4(x))
        # x = self.linear(x.view(-1, 32 * 5 * 5))
        x = self.linear(x.reshape(-1, 32 * 5 * 5))
        return x
        # return self.critic_linear(x), self.actor_linear(x)

    def pi(self, x):
        return self.actor_linear(x)

    def value(self, x):
        return self.critic_linear(x)

