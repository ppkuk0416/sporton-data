"""Small CPU PyTorch multitask MLP, calibrated only on a separate time block."""
import math
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression


class TorchMultitask:
    def __init__(self,epochs=80,seed=2026):
        if not 1<=epochs<=200:raise ValueError('epochs must be between 1 and 200')
        self.epochs=epochs;self.seed=seed

    def fit(self,fit,cal,features,outcomes):
        try:import torch
        except ImportError:raise ValueError('PyTorch CPU dependency missing; run bash tools/install.sh') from None
        from torch import nn
        torch.set_num_threads(2);torch.manual_seed(self.seed)
        torch.use_deterministic_algorithms(True)
        self.features=list(features);self.outcomes=list(outcomes)
        self.scaler=StandardScaler().fit(np.array([r['x'] for r in fit]))
        self.score_scaler=StandardScaler().fit(np.array([r['scores'] for r in fit]))
        X=torch.tensor(self.scaler.transform([r['x'] for r in fit]),dtype=torch.float32)
        y=torch.tensor([r['y'] for r in fit],dtype=torch.long)
        scores=torch.tensor(self.score_scaler.transform([r['scores'] for r in fit]),dtype=torch.float32)
        if set(y.tolist())!=set(range(len(outcomes))) or {r['y'] for r in cal}!=set(range(len(outcomes))):
            raise ValueError('all outcome classes required in fit/calibration')
        self.network=nn.Sequential(nn.Linear(len(features),32),nn.ReLU(),nn.Linear(32,16),nn.ReLU(),nn.Linear(16,len(outcomes)+2))
        opt=torch.optim.Adam(self.network.parameters(),lr=.003,weight_decay=.005)
        self.loss_history=[]
        for epoch in range(self.epochs):
            # Fixed hyperparameters; no validation/test feedback or random time splitting.
            self.network.train();pred=self.network(X)
            loss=nn.functional.cross_entropy(pred[:,:len(outcomes)],y)+.25*nn.functional.smooth_l1_loss(pred[:,len(outcomes):],scores)
            if not torch.isfinite(loss):raise ValueError('non-finite deep learning loss')
            opt.zero_grad();loss.backward();torch.nn.utils.clip_grad_norm_(self.network.parameters(),5.);opt.step()
            self.loss_history.append(float(loss.detach()))
        self.network.eval();CX=np.array([r['x'] for r in cal])
        raw=self.raw_probabilities(CX)
        self.calibrator=LogisticRegression(C=1.,max_iter=1000,random_state=self.seed).fit(np.log(np.clip(raw,1e-9,1)),[r['y'] for r in cal])
        residual=np.abs(np.array([r['scores'] for r in cal])-self.scores(CX))
        self.radius=np.quantile(residual,min(1,math.ceil((len(cal)+1)*.9)/len(cal)),axis=0,method='higher')
        self.torch_version=torch.__version__
        return self

    def output(self,X):
        import torch
        X=np.asarray(X,dtype=float)
        if X.ndim!=2 or X.shape[1]!=len(self.features) or not np.isfinite(X).all():raise ValueError('invalid MLP feature matrix')
        self.network.eval()
        with torch.inference_mode():return self.network(torch.tensor(self.scaler.transform(X),dtype=torch.float32)).numpy()

    def raw_probabilities(self,X):
        logits=self.output(X)[:,:len(self.outcomes)].astype(float);logits-=logits.max(axis=1,keepdims=True)
        p=np.exp(logits);return p/p.sum(axis=1,keepdims=True)

    def probabilities(self,X):return self.calibrator.predict_proba(np.log(np.clip(self.raw_probabilities(X),1e-9,1)))
    def scores(self,X):return np.maximum(0,self.score_scaler.inverse_transform(self.output(X)[:,len(self.outcomes):]))
