import torch
from PIL import Image
from torchvision import transforms, datasets
from torch.utils.data import Dataset
from torch.utils.data import DataLoader
from torch.utils.data.distributed import DistributedSampler

from lipsim.core.data import NightDataset, BAPPSDataset
from lipsim.core.data.coco_datast import COCODataset


class DataAugmentationDINO(object):
    def __init__(self, global_crops_scale=None, local_crops_scale=None, local_crops_number=None):
        flip_and_color_jitter = transforms.Compose([
            transforms.RandomHorizontalFlip(p=0.5),
            transforms.RandomApply(
                [transforms.ColorJitter(brightness=0.4, contrast=0.4, saturation=0.2, hue=0.1)],
                p=0.8
            ),
            transforms.RandomGrayscale(p=0.2),
        ])
        self.standard_transform = transforms.Compose([
            transforms.CenterCrop(224),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
        ])
        # first global crop
        self.global_transfo1 = transforms.Compose([
            transforms.CenterCrop(224),
            flip_and_color_jitter,
            transforms.ToTensor(),
        ])

    def __call__(self, image):
        images = []
        images.append(self.standard_transform(image))
        images.append(self.global_transfo1(image))
        images = torch.stack(images, dim=0)
        return images


class ImagenetDataset(Dataset):
    def __init__(self, config, batch_size, is_training, is_distributed=False, num_workers=0, world_size=1):
        self.config = config
        self.batch_size = batch_size
        self.is_training = is_training
        self.is_distributed = is_distributed
        self.world_size = world_size
        self.num_workers = num_workers
        self.n_classes = 768
        self.height, self.width = 224, 500
        self.n_train_files = 1_281_167
        self.n_test_files = 50_000
        self.img_size = (None, 3, 224, 500)
        self.split = 'train' if self.is_training else 'val'

        self.means = (0.0000, 0.0000, 0.0000)
        self.stds = (1.0000, 1.0000, 1.0000)

        self.samples = []
        self.targets = []
        self.transform = {
            # 'train': transforms.Compose([
            #     transforms.CenterCrop(224),
            #     transforms.RandomHorizontalFlip(),
            #     transforms.ToTensor(),
            # ]),
            'train': DataAugmentationDINO(
                global_crops_scale=(0.4, 1.),
                local_crops_scale=(0.05, 0.4),
                local_crops_number=8
            ),
            'val': transforms.Compose([
                transforms.CenterCrop(224),
                transforms.ToTensor(),
            ])
        }

    def get_dataloader(self, shuffle=None):
        sampler = None
        if not shuffle:
            shuffle = True if self.is_training and not self.is_distributed else False
        dataset = datasets.ImageFolder(self.config.data_dir, transform=self.transform[self.split])
        if self.is_distributed:
            sampler = DistributedSampler(dataset, shuffle=False, num_replicas=self.world_size)

        data_loader = DataLoader(dataset, sampler=sampler, batch_size=self.batch_size, shuffle=shuffle,
                                 num_workers=self.num_workers, pin_memory=True, drop_last=True)
        return data_loader, sampler


readers_config = {
    'imagenet-1k': ImagenetDataset,
    'night': NightDataset,
    'bapps': BAPPSDataset,
    'coco': COCODataset
}
