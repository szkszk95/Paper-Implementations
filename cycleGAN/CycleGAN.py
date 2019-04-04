from __future__ import print_function
import argparse
import os
import random
import torch
import torch.nn as nn
import torch.backends.cudnn as cudnn
import torch.optim as optim
import torchvision.transforms as transforms
import torchvision.utils as vutils
from torch.autograd import Variable
from itertools import chain

from utils.dataset import DATASET
from utils.ImagePool import ImagePool
from model.Discriminator import Discriminator
from model.Generator import Generator

parser = argparse.ArgumentParser(description='train pix2pix model')
parser.add_argument('--batchSize', type=int, default=1, help='with batchSize=1 equivalent to instance normalization.')
parser.add_argument('--ngf', type=int, default=64)
parser.add_argument('--ndf', type=int, default=64)
parser.add_argument('--niter', type=int, default=40000, help='number of iterations to train for')
parser.add_argument('--lr', type=float, default=0.0002, help='learning rate, default=0.0002')
parser.add_argument('--beta1', type=float, default=0.5, help='beta1 for adam. default=0.5')
parser.add_argument('--weight_decay', type=float, default=1e-4, help='weight decay in network D, default=1e-4')
parser.add_argument('--cuda', action='store_true', help='enables cuda')
parser.add_argument('--outf', default='checkpoints/', help='folder to output images and model checkpoints')
parser.add_argument('--manualSeed', type=int, help='manual seed')
parser.add_argument('--dataPath', default='', help='path to training images')
parser.add_argument('--loadSize', type=int, default=143, help='scale image to this size')
parser.add_argument('--fineSize', type=int, default=128, help='random crop image to this size')
parser.add_argument('--flip', type=int, default=1, help='1 for flipping image randomly, 0 for not')
parser.add_argument('--input_nc', type=int, default=3, help='channel number of input image')
parser.add_argument('--output_nc', type=int, default=3, help='channel number of output image')
parser.add_argument('--G_AB', default='', help='path to pre-trained G_AB')
parser.add_argument('--G_BA', default='', help='path to pre-trained G_BA')
parser.add_argument('--save_step', type=int, default=20000, help='save interval')
parser.add_argument('--log_step', type=int, default=100, help='log interval')
parser.add_argument('--loss_type', default='mse', help='GAN loss type, bce|mse default is negative likelihood loss')
parser.add_argument('--poolSize', type=int, default=50,
                    help='size of buffer in lsGAN, poolSize=0 indicates not using history')
parser.add_argument('--lambda_ABA', type=float, default=10.0, help='weight of cycle loss ABA')
parser.add_argument('--lambda_BAB', type=float, default=10.0, help='weight of cycle loss BAB')

opt = parser.parse_args()
print(opt)

try:
    os.makedirs(opt.outf)
except OSError:
    pass

if opt.manualSeed is None:
    opt.manualSeed = random.randint(1, 10000)
print("Random Seed: ", opt.manualSeed)
random.seed(opt.manualSeed)
torch.manual_seed(opt.manualSeed)
if opt.cuda:
    torch.cuda.manual_seed_all(opt.manualSeed)

cudnn.benchmark = True
##########   DATASET   ###########
datasetA = DATASET(os.path.join(opt.dataPath, '/home/szk/PycharmProjects/open-reid/examples/data/cuhk03/images'), opt.loadSize, opt.fineSize, opt.flip)
datasetB = DATASET(os.path.join(opt.dataPath, '/home/szk/PycharmProjects/open-reid/examples/data/viper/images'), opt.loadSize, opt.fineSize, opt.flip)
loader_A = torch.utils.data.DataLoader(dataset=datasetA,
                                       batch_size=opt.batchSize,
                                       shuffle=True,
                                       num_workers=2)
loaderA = iter(loader_A)
loader_B = torch.utils.data.DataLoader(dataset=datasetB,
                                       batch_size=opt.batchSize,
                                       shuffle=True,
                                       num_workers=2)
loaderB = iter(loader_B)
ABPool = ImagePool(opt.poolSize)
BAPool = ImagePool(opt.poolSize)


# ---------   MODEL   ---------
# custom weights initialization called on netG and netD
def weights_init(m):
    classname = m.__class__.__name__
    if classname.find('Conv') != -1:
        m.weight.data.normal_(0.0, 0.02)
    elif classname.find('BatchNorm') != -1:
        m.weight.data.normal_(1.0, 0.02)
        m.bias.data.fill_(0)


ndf = opt.ndf
ngf = opt.ngf
nc = 3

D_A = Discriminator(opt.input_nc, ndf)
D_B = Discriminator(opt.output_nc, ndf)
G_AB = Generator(opt.input_nc, opt.output_nc, opt.ngf)
G_BA = Generator(opt.output_nc, opt.input_nc, opt.ngf)

if (opt.G_AB != ''):
    print('Warning! Loading pre-trained weights.')
    G_AB.load_state_dict(torch.load(opt.G_AB))
    G_BA.load_state_dict(torch.load(opt.G_BA))
else:
    G_AB.apply(weights_init)
    G_BA.apply(weights_init)

if (opt.cuda):
    D_A.cuda()
    D_B.cuda()
    G_AB.cuda()
    G_BA.cuda()

D_A.apply(weights_init)
D_B.apply(weights_init)

# --------- LOSS & OPTIMIZER ---------
criterionMSE = nn.L1Loss()
if (opt.loss_type == 'bce'):
    criterion = nn.BCELoss()
else:
    criterion = nn.MSELoss()
# chain is used to update two generators simultaneously
optimizerD_A = torch.optim.Adam(D_A.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999), weight_decay=opt.weight_decay)
optimizerD_B = torch.optim.Adam(D_B.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999), weight_decay=opt.weight_decay)
optimizerG = torch.optim.Adam(chain(G_AB.parameters(), G_BA.parameters()), lr=opt.lr, betas=(opt.beta1, 0.999))

# --------- GLOBAL VARIABLES ---------
input_nc = opt.input_nc
output_nc = opt.output_nc
fineSize = opt.fineSize

real_A = torch.FloatTensor(opt.batchSize, input_nc, fineSize, fineSize)
AB = torch.FloatTensor(opt.batchSize, input_nc, fineSize, fineSize)
real_B = torch.FloatTensor(opt.batchSize, output_nc, fineSize, fineSize)
BA = torch.FloatTensor(opt.batchSize, output_nc, fineSize, fineSize)
label = torch.FloatTensor(opt.batchSize)

real_A = Variable(real_A)
real_B = Variable(real_B)
label = Variable(label)
AB = Variable(AB)
BA = Variable(BA)

if (opt.cuda):
    real_A = real_A.cuda()
    real_B = real_B.cuda()
    label = label.cuda()
    AB = AB.cuda()
    BA = BA.cuda()
    criterion.cuda()
    criterionMSE.cuda()

real_label = 1
fake_label = 0


###########   Testing    ###########
def test(niter):
    loaderA, loaderB = iter(loader_A), iter(loader_B)
    imgA = loaderA.next()
    imgB = loaderB.next()
    real_A.data.resize_(imgA.size()).copy_(imgA)
    real_B.data.resize_(imgB.size()).copy_(imgB)
    AB = G_AB(real_A)
    BA = G_BA(real_B)

    vutils.save_image(AB.data,
                      'samples/reid/AB_niter_%03d_1.png' % (niter),
                      normalize=True)
    vutils.save_image(BA.data,
                      'samples/reid/BA_niter_%03d_1.png' % (niter),
                      normalize=True)

    imgA = loaderA.next()
    imgB = loaderB.next()
    real_A.data.resize_(imgA.size()).copy_(imgA)
    real_B.data.resize_(imgB.size()).copy_(imgB)
    AB = G_AB(real_A)
    BA = G_BA(real_B)

    vutils.save_image(AB.data,
                      'AB_niter_%03d_2.png' % (niter),
                      normalize=True)
    vutils.save_image(BA.data,
                      'BA_niter_%03d_2.png' % (niter),
                      normalize=True)


import time


# ---------   Training ---------
D_A.train()
D_B.train()
G_AB.train()
G_BA.train()
for iteration in range(1, opt.niter + 1):
    # --------- data ---------
    try:
        imgA = loaderA.next()
        imgB = loaderB.next()
    except StopIteration:
        loaderA, loaderB = iter(loader_A), iter(loader_B)
        imgA = loaderA.next()
        imgB = loaderB.next()

    # real_A.data.resize_(imgA.size()).copy_(imgA)
    # real_B.data.resize_(imgB.size()).copy_(imgB)

    t = time.time()

    real_A = imgA
    real_B = imgB

    # --------- fDx ---------
    D_A.zero_grad()
    D_B.zero_grad()

    # train with real
    outA = D_A(real_A)
    outB = D_B(real_B)
    label.data.resize_(outA.size())
    label.data.fill_(real_label)
    l_A = criterion(outA, label)
    l_B = criterion(outB, label)
    errD_real = l_A + l_B

    print(time.time()-t)
    t = time.time()
    # errD_real.backward()

    # train with fake
    label.data.fill_(fake_label)

    AB_tmp = G_AB(real_A)
    AB.data.resize_(AB_tmp.data.size()).copy_(ABPool.Query(AB_tmp.cpu().data))
    BA_tmp = G_BA(real_B)
    BA.data.resize_(BA_tmp.data.size()).copy_(BAPool.Query(BA_tmp.cpu().data))

    print(time.time() - t)
    t = time.time()

    out_BA = D_A(BA.detach())
    out_AB = D_B(AB.detach())

    l_BA = criterion(out_BA, label)
    l_AB = criterion(out_AB, label)

    errD_fake = l_BA + l_AB
    # errD_fake.backward()

    errD = (errD_real + errD_fake) * 0.5
    errD.backward()
    optimizerD_A.step()
    optimizerD_B.step()
    print(time.time() - t)
    t = time.time()

    # --------- fGx ---------
    G_AB.zero_grad()
    G_BA.zero_grad()
    label.data.fill_(real_label)

    AB = G_AB(real_A)
    ABA = G_BA(AB)

    BA = G_BA(real_B)
    BAB = G_AB(BA)

    out_BA = D_A(BA)
    out_AB = D_B(AB)

    l_BA = criterion(out_BA, label)
    l_AB = criterion(out_AB, label)

    # reconstruction loss
    l_rec_ABA = criterionMSE(ABA, real_A) * opt.lambda_ABA
    l_rec_BAB = criterionMSE(BAB, real_B) * opt.lambda_BAB

    errGAN = l_BA + l_AB
    errMSE = l_rec_ABA + l_rec_BAB
    errG = errGAN + errMSE
    errG.backward()

    optimizerG.step()

    print(time.time() - t)
    t = time.time()

    # ---------  Logging  ---------
    if iteration % opt.log_step:
        print('[%d/%d] Loss_D: %.4f Loss_G: %.4f Loss_MSE: %.4f'
              % (iteration, opt.niter,
                 errD.data[0], errGAN.data[0], errMSE.data[0]))
    # --------- Visualize ---------
    if iteration % 1000 == 0:
        test(iteration)

    if iteration % opt.save_step == 0:
        torch.save(G_AB.state_dict(), '{}/G_AB_{}.pth'.format(opt.outf, iteration))
        torch.save(G_BA.state_dict(), '{}/G_BA_{}.pth'.format(opt.outf, iteration))
        torch.save(D_A.state_dict(), '{}/D_A_{}.pth'.format(opt.outf, iteration))
        torch.save(D_B.state_dict(), '{}/D_B_{}.pth'.format(opt.outf, iteration))
