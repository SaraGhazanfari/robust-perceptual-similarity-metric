import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
import torch.optim as optim
from torchvision.transforms import transforms
from tqdm import tqdm


def normalize_fn(dataset):
    if dataset == 'imagenet_val':
        normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    else:
        normalize = transforms.Normalize(mean=[0.491, 0.482, 0.446], std=[0.202, 0.199, 0.201])

    return normalize


class SSAH(nn.Module):
    """"
    Parameters:
    -----------

    """

    def __init__(self,
                 model: nn.Module,
                 num_iteration: int = 100,
                 learning_rate: float = 0.01,
                 device: torch.device = torch.device('cuda'),
                 Targeted: bool = False,
                 dataset: str = 'cifar10',
                 m: float = 0,  # 0.2
                 alpha: float = 1,
                 lambda_lf: float = 1,  # 0.1,
                 wave: str = 'haar', ) -> None:
        super(SSAH, self).__init__()
        self.model = model
        self.device = device
        self.lr = learning_rate
        self.target = Targeted
        self.num_iteration = num_iteration
        self.dataset = dataset
        self.m = m
        self.alpha = alpha
        self.lambda_lf = lambda_lf

        self.normalize_fn = normalize_fn(self.dataset)

        self.DWT = DWT_2D_tiny(wavename=wave)
        self.IDWT = IDWT_2D_tiny(wavename=wave)
        print('num_iteration:', num_iteration, ' learning_rate:', learning_rate, ' alpha:', alpha, ' margin', m)

    def fea_extract(self, inputs: torch.Tensor) -> torch.Tensor:
        fea = self.model(inputs)
        return fea

    def cal_sim(self, adv, inputs):
        adv = F.normalize(adv, dim=1)
        inputs = F.normalize(inputs, dim=1)

        r, c = inputs.shape
        sim_matrix = torch.matmul(adv, inputs.T)
        mask = torch.eye(r, dtype=torch.bool).to(self.device)
        pos_sim = sim_matrix[mask].view(r, -1)
        neg_sim = sim_matrix.view(r, -1)
        return pos_sim, neg_sim

    def select_setp1(self, pos_sim, neg_sim):
        neg_sim, indices = torch.sort(neg_sim, descending=True)
        pos_neg_sim = torch.cat([pos_sim, neg_sim[:, -1].view(pos_sim.shape[0], -1)], dim=1)
        return pos_neg_sim, indices

    def select_step2(self, pos_sim, neg_sim, indices):
        hard_sample = indices[:, -1]
        ones = torch.sparse.torch.eye(neg_sim.shape[1]).to(self.device)
        hard_one_hot = ones.index_select(0, hard_sample).bool()
        hard_sim = neg_sim[hard_one_hot].view(neg_sim.shape[0], -1)
        pos_neg_sim = torch.cat([pos_sim, hard_sim], dim=1)
        return pos_neg_sim

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:

        with torch.no_grad():
            inputs_fea = self.fea_extract(self.normalize_fn(inputs))

        # low frequency component
        inputs_ll = self.DWT(inputs)
        inputs_ll = self.IDWT(inputs_ll)

        # changes of variables
        eps = 3e-7
        modifier = torch.arctanh(inputs * (2 - eps * 2) - 1 + eps)
        modifier = Variable(modifier, requires_grad=True)
        modifier = modifier.to(self.device)
        optimizer = optim.Adam([modifier], lr=self.lr)

        lowFre_loss = nn.SmoothL1Loss(reduction='sum')

        for step in tqdm(range(self.num_iteration)):
            optimizer.zero_grad()

            adv = 0.5 * (torch.tanh(modifier) + 1)
            adv_fea = self.fea_extract(self.normalize_fn(adv))

            adv_ll = self.DWT(adv)
            adv_ll = self.IDWT(adv_ll)

            pos_sim, neg_sim = self.cal_sim(adv_fea, inputs_fea)
            # select the most dissimilar one in the first iteration
            if step == 0:
                pos_neg_sim, indices = self.select_setp1(pos_sim, neg_sim)

            # record the most dissimilar ones by indices and calculate similarity
            else:
                pos_neg_sim = self.select_step2(pos_sim, neg_sim, indices)

            sim_pos = pos_neg_sim[:, 0]
            sim_neg = pos_neg_sim[:, -1]

            w_p = torch.clamp_min(sim_pos.detach() - self.m, min=0)
            w_n = torch.clamp_min(1 + self.m - sim_neg.detach(), min=0)

            adv_cost = torch.sum(torch.clamp(w_p * sim_pos - w_n * sim_neg, min=0))
            lowFre_cost = lowFre_loss(adv_ll, inputs_ll)
            # if adv_cost == 0:
            #     break
            total_cost = self.alpha * adv_cost  # + self.lambda_lf * lowFre_cost

            optimizer.zero_grad()
            total_cost.backward()
            optimizer.step()

        adv = 0.5 * (torch.tanh(modifier.detach()) + 1)
        return adv
