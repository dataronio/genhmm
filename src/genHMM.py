# file genHMM.py

import os
import sys
sys.path.append("..")

from tqdm import tqdm
import numpy as np
from scipy.special import logsumexp as lsexp
from sklearn.utils import check_array, check_random_state
from sklearn.utils.validation import check_is_fitted

from src.glow.models import Glow, FlowNet
from src.glow.config import JsonConfig
from src.realnvp import RealNVP

from functools import partial

#from tensorboardX import SummaryWriter

import torch
from torch import nn, distributions
from torch.autograd import Variable
from src._torch_hmmc import _compute_log_xi_sum, _forward, _backward


class GenHMMclassifier(nn.Module):
    def __init__(self, mdlc_files=None, **options):
        super(GenHMMclassifier, self).__init__()

        if mdlc_files == None:
            self.nclasses = options["nclasses"]
            self.hmms = [GenHMM(**options) for _ in range(self.nclasses)]
        else:
            self.hmms = [load_model(fname) for fname in mdlc_files]

        
    def forward(self, x, lengths=None):
        if lengths is None:
            # Assume we passed only one sequence.
            l = [x.shape[0]]

        else:
            l = lengths

        return [classHMM.pred_score(x, lengths=l)[0] for classHMM in self.hmms]



class GenHMM(torch.nn.Module):
    def __init__(self, n_states=None, n_prob_components=None, device='cpu', dtype=torch.FloatTensor, \
            EPS=1e-12, lr=None, em_skip=None, log_dir=None):
        super(GenHMM, self).__init__()

        self.n_states = n_states
        self.dtype = dtype
        self.n_prob_components = n_prob_components
 
        self.device=device
        self.dtype=dtype
        self.EPS = EPS
        self.lr = lr
        self.em_skip = em_skip
        self.log_dir = log_dir

        # Initialize HMM parameters
        self.init_transmat()
        self.init_startprob()
        
        # Initialize generative model networks
        self.init_gen()
        
        # training log directory and logger
        self.log_dir = log_dir
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)
    
    def init_startprob(self):
        """
        Initialize HMM initial coefficients.
        """
        init = 1. / self.n_states
        self.startprob_ = torch.ones(self.n_states) * init
        return self

    def init_transmat(self):
        """
        Initialize HMM transition matrix.
        """
        init = 1/self.n_states
        self.transmat_ = torch.ones(self.n_states, self.n_states) * init
        return self

    def init_gen(self):

        """
        Initialize HMM probabilistic model.
        """
        H = 28
        D = 14
        nchain = 3
        d = D // 2

        nets = lambda: nn.Sequential(nn.Linear(D, H), nn.LeakyReLU(), nn.Linear(H, H), nn.LeakyReLU(), nn.Linear(H, D),
                                     nn.Tanh())
        nett = lambda: nn.Sequential(nn.Linear(D, H), nn.LeakyReLU(), nn.Linear(H, H), nn.LeakyReLU(), nn.Linear(H, D))
        
        masks = torch.from_numpy(np.array([[0]*d + [1]*(D-d), [1]*d + [0]*(D-d)] * nchain).astype(np.float32))
        ### torch MultivariateNormal logprob gets error when input is cuda tensor
        ### thus changing it to implementation
        prior = distributions.MultivariateNormal(torch.zeros(D).to(self.device), torch.eye(D).to(self.device))
        # prior = lambda x: GaussianDiag.logp(torch.zeros(D), torch.zeros(D), x)
        self.flow = RealNVP(nets, nett, masks, prior)


        #  Init mixture
        self.pi = self.dtype( np.random.rand(self.n_states, self.n_prob_components) )
        normalize(self.pi, axis=1)

        # Init networks
        self.networks = [RealNVP(nets, nett, masks, prior) for _ in range(self.n_prob_components*self.n_states)]

        # Reshape in a n_states x n_prob_components array
        self.networks = np.array(self.networks).reshape(self.n_states, self.n_prob_components)
        return self
     
    def _initialize_sufficient_statistics(self):
        """Initializes sufficient statistics required for M-step.

        The method is *pure*, meaning that it doesn't change the state of
        the instance.  For extensibility computed statistics are stored
        in a dictionary.

        Returns
        -------
        nobs : int
            Number of samples in the data.

        start : array, shape (n_components, )
            An array where the i-th element corresponds to the posterior
            probability of the first sample being generated by the i-th
            state.

        trans : array, shape (n_components, n_components)
            An array where the (i, j)-th element corresponds to the
            posterior probability of transitioning between the i-th to j-th
            states.
        """

        stats = {'nobs': 0,
                 'nframes':0,
                 'start': torch.zeros(self.n_states).to(self.device),
                 'trans': torch.zeros(self.n_states, self.n_states).to(self.device),
                 'mixture': torch.zeros(self.n_states, self.n_prob_components).to(self.device),
                 'loss': torch.FloatTensor([0]).to(self.device)
                 }

        self.stats = stats

   
    def _accumulate_sufficient_statistics(self, framelogprob,
                                          posteriors, fwdlattice, bwdlattice, loglh_sk):
        """Updates sufficient statistics from a given sample.

        Parameters
        ----------
        stats : dict
            Sufficient statistics as returned by
            :meth:`~base._BaseHMM._initialize_sufficient_statistics`.

        framelogprob : array, shape (batch_size, n_samples, n_components)
            Log-probabilities of each sample under each of the model states.

        posteriors : array, shape (batch_size, n_samples, n_components)
            Posterior probabilities of each sample being generated by each
            of the model states.

        fwdlattice, bwdlattice : array, shape (batch_size, n_samples, n_components)
            Log-forward and log-backward probabilities.

        loglh_sk : array, shape (batch_size, n_components, n_prob_components, n_samples)
            Log-probabilities of each batch sample under each components of each states.
        """


        batch_size, n_samples, _ = framelogprob.shape

        self.stats['nframes'] += n_samples * batch_size
        self.stats['nobs'] += batch_size
        self.stats['start'] += posteriors[:,0].sum(0)

        
        
        log_xi_sum = _compute_log_xi_sum(n_samples, n_components, fwdlattice,
                                         torch.log(self.transmat_ + self.EPS),
                                         bwdlattice, framelogprob,
                                         torch.ones(n_components, n_components, device=self.device)*float('-inf'))
            # _log_xi_sum = _compute_log_xi_sum(n_samples, n_components,\
            #                                    self.dtype(fwdlattice).to(self.device),\
            #                                    self.dtype(log_mask_zero(self.transmat_)).to(self.device),\
            #                                    self.dtype(bwdlattice).to(self.device), \
            #                                    self.dtype(framelogprob).to(self.device),\
            #                                    self.dtype(np.full((n_components, n_components), -np.inf)).to(self.device))

            
        self.stats['trans'] += torch.exp(log_xi_sum)

 #       print(loglh_sk.shape, self.n_states, self.n_prob_components)
        # max_loglh = torch.max(torch.max(loglh_sk, dim=1)[0],dim=1)[0]
        max_loglh = torch.max(torch.max(loglh_sk, dim=3)[0], dim=2)[0]
#        print(max_loglh.shape)

        gamma_ = torch.zeros(batch_size, self.n_states, self.n_prob_components, n_samples, device=self.device)

        for i in range(self.n_states):
            for t in range(n_samples):
                for m in range(self.n_prob_components):
                    gamma_[:,i, m, t] = self.pi[i, m] * (loglh_sk - max_loglh[:,i].reshape(-1,1,1,1))[:, i, m, t].exp()
                
 #               print(gamma_[:, i, :, t].shape,  gamma_[:,i, :, t].sum(1).shape) 
                gamma_[:, i, :, t] /= (gamma_[:,i, :, t].sum(1).reshape(-1, 1) + 1e-6)   #.reshape(self.n_states,1,n_samples)
#                print(gamma_[:, i, :, t].shape, posteriors[:, t, i].shape)
 
                gamma_[:, i, :, t] *= posteriors[:, t, i].reshape(-1,1)

        self.stats["mixture"] += gamma_.sum(3).sum(0)

        return



    def _do_forward_pass(self, framelogprob):
        batch_size, n_samples, n_components = framelogprob.shape
        # in case log computation encounter log(0), do log(x + self.EPS)
        
        
        ### To Do: matain hmm parameters as torch tensors
        log_startprob = torch.log(self.dtype(self.startprob_).to(self.device) + self.EPS)
        log_transmat = torch.log(self.dtype(self.transmat_).to(self.device) + self.EPS)

        return _forward(n_samples, n_components, log_startprob, log_transmat, framelogprob) 

    def _do_backward_pass(self, framelogprob):
        batch_size, n_samples, n_components = framelogprob.shape
        
        ### To Do: matain hmm parameters as torch tensors
        log_startprob = torch.log(self.dtype(self.startprob_).to(self.device) + self.EPS)
        log_transmat = torch.log(self.dtype(self.transmat_).to(self.device) + self.EPS)
        return _backward(n_samples, n_components, log_startprob,\
                         log_transmat, framelogprob)
        
    def _compute_posteriors(self, fwdlattice, bwdlattice):
        # gamma is guaranteed to be correctly normalized by logprob at
        # all frames, unless we do approximate inference using pruning.
        # So, we will normalize each frame explicitly in case we
        # pruned too aggressively.
        log_gamma = fwdlattice + bwdlattice
        # Normalizes the input array so that the exponent of the sum is 1
        lse_gamma = torch.logsumexp(log_gamma, dim=2)
        log_gamma -= lse_gamma[:,:, None]
        
        return torch.exp(log_gamma)

    def pred_score(self, X, lengths=None):
        """ Update the base score method, such that the scores of sequences are returned
        score: the log probability under the model.
        Parameters
        ----------
        X : array-like, shape (n_samples, n_features)
            Feature matrix of individual samples.
        lengths : array-like of integers, shape (n_sequences, ), optional
            Lengths of the individual sequences in ``X``. The sum of
            these should be ``n_samples``.
        Returns
        -------
        logprob : list of floats, [logprob1, logprob2, ... ]
            Log likelihood of ``X``.
        """

        logprob = self.forward(X, testing=True)
        return logprob


    def forward(self, x, testing=False):
        """PYTORCH FORWARD, NOT HMM forward algorithm. This function is called for each batch.
        Input: batch of sequences, array size, (batch_size, n_samples, n_dimensions)
        Output: Loss, scaler
        """
        if self.update_HMM:
            self._initialize_sufficient_statistics()

        batch_size = x.shape[0]
        n_samples = x.shape[1]

        llh = torch.zeros(batch_size, n_samples, self.n_states).to(self.device)
        self.loglh_sk = torch.zeros((batch_size, self.n_states, self.n_prob_components, n_samples)).to(self.device)

        for s in range(self.n_states):
            loglh_sk = [self.networks[s, k].log_prob(x).reshape(batch_size, 1, -1)/x.numel() for k in range(self.n_prob_components)]
            ll = torch.cat(loglh_sk, dim=1)
            self.loglh_sk[:,s,:,:] = ll
            llh[:,:,s] = (self.logPIk_s[s].reshape(1,self.n_prob_components, 1) + ll).detach().sum(1)
        
        logprob, fwdlattice = self._do_forward_pass(llh)
        if testing:
            return logprob


        bwdlattice = self._do_backward_pass(llh)
        posteriors = self._compute_posteriors(fwdlattice, bwdlattice)
        
        if self.update_HMM:
            self._accumulate_sufficient_statistics(x, llh, posteriors, fwdlattice, bwdlattice, self.loglh_sk)

        # Compute loss associated with sequence
        logPIk_s_ext = self.logPIk_s.reshape(1, self.n_states, self.n_prob_components, 1)
        
        # Brackets = log-P(X | chi, S) + log-P(chi | s)
        brackets = self.loglh_sk + logPIk_s_ext

        # Compute log-p(chi | s, X) = log-P(X|s,chi) + log-P(chi|s) - log\sum_{chi} exp ( log-P(X|s,chi) + log-P(chi|s) )
        log_num = self.loglh_sk.detach() + logPIk_s_ext
        log_denom = torch.logsumexp(self.loglh_sk.detach() + logPIk_s_ext, dim=2)

        logpk_sX = log_num - log_denom.reshape(batch_size, self.n_states, 1, n_samples)

        post = posteriors.transpose(1,2)
        #  The .sum(2) call sums on the components and .sum(1).sum(1) sums on all states and samples
        loss = -(post * (torch.exp(logpk_sX) * brackets).sum(2)).sum(1).sum(1)/(n_samples*batch_size)
        return loss.sum()


    def fit(self, traindata):
        """Performs one EM step and `em_skip` backprops before returning. The optimizer is re-initialized after each EM step.
            Follow the loss in stderr
            Input : traindata : torch.data.DataLoader object wrapping the batches.
            Output : None
        """

        optimizer = torch.optim.Adam(
            sum([[p for p in flow.parameters() if p.requires_grad == True] for flow in self.networks.reshape(-1).tolist()], []), lr=self.lr)
        

        for i in range(self.em_skip):
            # if i is the index of last loop, set update_HMM as true
            if i == self.em_skip-1:
                self.update_HMM = True
            else:
                self.update_HMM = False

            optimizer.zero_grad()
            for b, data in enumerate(traindata):
                # start = dt.now()
                loss = self.forward(data, testing=False)
                loss.backward()

            print("i:{}\tb:{}\tLoss:{}".format(i, b, loss.data), file=sys.stderr)
            optimizer.step()
    
        # Perform EM step
        # Update initial proba
        startprob_ = self.startprob_prior - 1.0 + self.stats['start']
        self.startprob_ = torch.where(self.startprob_ == 0.0,
                                   self.startprob_, startprob_)
        normalize(self.startprob_, axis=0)
        
        # Update transition
        transmat_ = self.transmat_prior - 1.0 + self.stats['trans']
        self.transmat_ = torch.where(self.transmat_ == 0.0,
                                  self.transmat_, transmat_)
        normalize(self.transmat_, axis=1)
        
        # Update prior
        self.pi = self.stats["mixture"]

        # In case we get a line of zeros in the stats
        #self.pi[self.pi.sum(1) == 0, :] = np.ones(self.n_prob_components) / self.n_prob_components
        normalize(self.pi, axis=1)
    


class wrapper(torch.nn.Module):
    def __init__(self, mdl):
        super(wrapper, self).__init__()
        self.userdata = mdl


def save_model(mdl, fname=None):
    torch.save(wrapper(mdl), fname)
    return 0

def load_model(fname):
    savable = torch.load(fname)
    return savable.userdata


def normalize(a, axis=None):
    """Normalizes the input array so that it sums to 1.

    Parameters
    ----------
    a : array
        Non-normalized input data.

    axis : int
        Dimension along which normalization is performed.

    Notes
    -----
    Modifies the input **inplace**.
    """
    a_sum = a.sum(axis, keepdim=True)
    a_sum[a_sum==0] = 1
    a /= a_sum


