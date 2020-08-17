import os
import sys
sys.path.append(os.path.join( os.path.dirname(__file__), os.path.pardir ))

import torch
import torch.nn as nn

from tqdm import trange

import numpy as np
from sklearn.metrics import mean_squared_error

from data_process.util import savebest_checkpoint

import logging
logger = logging.getLogger('ConvRNN.Net')

class ConvRNN(nn.Module):
    def __init__(self, input_dim, timesteps, output_dim, kernel_size1=7, kernel_size2=5, kernel_size3=3, 
                 n_channels1=32, n_channels2=32, n_channels3=32, n_units1=32, n_units2=32, n_units3=32, params = None):
        super().__init__()
        self.avg_pool1 = nn.AvgPool1d(2, 2)
        self.avg_pool2 = nn.AvgPool1d(4, 4)
        # input_dim denotes input_channel and is going to be transformed to nn_channels1, output_step = input_step - (kernel_size -1), where the input_step has been left padded with kernel_size - 1, thus the output_step should be equal to original timesteps
        self.conv11 = nn.Conv1d(input_dim, n_channels1, kernel_size=kernel_size1)
        # channel transformation, samely outputs the same timesteps with left padding.
        self.conv12 = nn.Conv1d(n_channels1, n_channels1, kernel_size=kernel_size1)
        self.conv21 = nn.Conv1d(input_dim, n_channels2, kernel_size=kernel_size2)
        self.conv22 = nn.Conv1d(n_channels2, n_channels2, kernel_size=kernel_size2)
        self.conv31 = nn.Conv1d(input_dim, n_channels3, kernel_size=kernel_size3)
        self.conv32 = nn.Conv1d(n_channels3, n_channels3, kernel_size=kernel_size3)
        
        # using the output_channel (n_channels1) as the input_size in each time steps, and using n_units1 to denote the hidden_size
        self.gru1 = nn.GRU(n_channels1, n_units1, batch_first=True)
        self.gru2 = nn.GRU(n_channels2, n_units2, batch_first=True)
        self.gru3 = nn.GRU(n_channels3, n_units3, batch_first=True)
        self.linear1 = nn.Linear(n_units1+n_units2+n_units3, output_dim)
        self.linear2 = nn.Linear(input_dim*timesteps, output_dim)
        # padding left (kernel_size1-1) step with value 0, padding right 0 step with value 0
        self.zp11 = nn.ConstantPad1d(((kernel_size1-1), 0), 0)
        self.zp12 = nn.ConstantPad1d(((kernel_size1-1), 0), 0)
        self.zp21 = nn.ConstantPad1d(((kernel_size2-1), 0), 0)
        self.zp22 = nn.ConstantPad1d(((kernel_size2-1), 0), 0)
        self.zp31 = nn.ConstantPad1d(((kernel_size3-1), 0), 0)
        self.zp32 = nn.ConstantPad1d(((kernel_size3-1), 0), 0)
        
        self.optimizer=torch.optim.Adam(self.parameters(), lr=0.001)
        self.epoch_scheduler = torch.optim.lr_scheduler.StepLR(self.optimizer, 20, gamma=0.9)
        self.loss = nn.MSELoss()
        self.params = params

    def forward(self, x):
        x = x.permute(0, 2, 1)
        # line1
        y1 = self.zp11(x)
        y1 = torch.relu(self.conv11(y1))
        y1 = self.zp12(y1)
        y1 = torch.relu(self.conv12(y1))
        y1 = y1.permute(0, 2, 1)
        out, h1 = self.gru1(y1)
        # line2
        y2 = self.avg_pool1(x)
        y2 = self.zp21(y2)
        y2 = torch.relu(self.conv21(y2))
        y2 = self.zp22(y2)
        y2 = torch.relu(self.conv22(y2))
        y2 = y2.permute(0, 2, 1)
        out, h2 = self.gru2(y2)
        # line3 
        y3 = self.avg_pool2(x)
        y3 = self.zp31(y3)
        y3 = torch.relu(self.conv31(y3))
        y3 = self.zp32(y3)
        y3 = torch.relu(self.conv32(y3))
        y3 = y3.permute(0, 2, 1)
        out, h3 = self.gru3(y3)
        h = torch.cat([h1[-1], h2[-1], h3[-1]], dim=1)
        out1 = self.linear1(h)
        out2 = self.linear2(x.contiguous().view(x.shape[0], -1))
        out = out1 + out2
        return out

    def xfit(self, train_loader, val_loader, params):
        # update self.params
        self.params = params
        min_vmse = 9999
        for i in trange(self.params.epochs):
            mse_train = 0
            for batch_x, batch_y in train_loader :
                batch_x = batch_x.to(self.params.device)
                batch_y = batch_y.to(self.params.device)
                opt.zero_grad()
                y_pred = self(batch_x)
                y_pred = y_pred.squeeze(1)
                l = self.loss(y_pred, batch_y)
                l.backward()
                mse_train += l.item()*batch_x.shape[0]
                self.optimizer.step()
            self.epoch_scheduler.step()
            with torch.no_grad():
                mse_val = 0
                preds = []
                true = []
                for batch_x, batch_y in val_loader:
                    batch_x = batch_x.to(self.params.device)
                    batch_y = batch_y.to(self.params.device)
                    output = self(batch_x)
                    output = output.squeeze(1)
                    preds.append(output.detach().cpu().numpy())
                    true.append(batch_y.detach().cpu().numpy())
                    mse_val += self.loss(output, batch_y).item()*batch_x.shape[0]
            preds = np.concatenate(preds)
            true = np.concatenate(true)

            if(i % 10 == 0):
                vmse = mean_squared_error(true, preds)
                logging.info('Current vmse: {}'.format(vmse))
                if vmse < min_vmse:
                    min_vmse = vmse
                    savebest_checkpoint({'state_dict':self.state_dict(),'optim_dict': self.optimizerimizer.state_dict()},checkpoint=self.params.model_dir)
                    logging.info('Best vmse: {}'.format(min_vmse))

    def predict(self,x,  using_best = True):
        '''
        x: (numpy.narray) shape: [sample, full-len, dim]
        return: (numpy.narray) shape: [sample, prediction-len]
        '''
        # test_batch: shape: [full-len, sample, dim]
        best_pth = os.path.join(self.params.model_dir, 'best.pth.tar')
        if os.path.exists(best_pth) and using_best:
            logger.info('Restoring best parameters from {}'.format(best_pth))
            load_checkpoint(best_pth,self,self.optimizer)
        
        x= torch.tensor(x).to(self.params.device)
        output = self(x)
        output = output.squeeze(1)
        pred = output.detach().cpu().numpy()

        return pred