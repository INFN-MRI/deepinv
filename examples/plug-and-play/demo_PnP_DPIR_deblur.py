"""
DPIR method for PnP image deblurring.
====================================================================================================

This example shows how to use the DPIR method to solve a PnP image deblurring problem. The DPIR method is described in
the following paper:
Zhang, K., Zuo, W., Gu, S., & Zhang, L. (2017). 
Learning deep CNN denoiser prior for image restoration. 
In Proceedings of the IEEE conference on computer vision and pattern recognition (pp. 3929-3938).
"""
import deepinv as dinv
from pathlib import Path
import torch
from torch.utils.data import DataLoader
from deepinv.models.denoiser import Denoiser
from deepinv.optim.data_fidelity import L2
from deepinv.optim.optimizers import optim_builder
from deepinv.training_utils import test
from torchvision import transforms
from deepinv.utils.parameters import get_DPIR_params
from deepinv.utils.demo import load_dataset, load_degradation

# %%
# Setup paths for data loading and results.
# ----------------------------------------------------------------------------------------
#

BASE_DIR = Path(".")
ORIGINAL_DATA_DIR = BASE_DIR / "datasets"
DATA_DIR = BASE_DIR / "measurements"
RESULTS_DIR = BASE_DIR / "results"
DEG_DIR = BASE_DIR / "degradations"
CKPT_DIR = BASE_DIR / "ckpts"


# %%
# Load base image datasets and degradation operators.
# ----------------------------------------------------------------------------------------
# In this example, we use the Set3C dataset and a motion blur kernel from
# `Levin et al. (2009) <https://ieeexplore.ieee.org/abstract/document/5206815/>`_.
#

# Set the global random seed from pytorch to ensure reproducibility of the example.
torch.manual_seed(0)

device = dinv.utils.get_freer_gpu() if torch.cuda.is_available() else "cpu"

# Set up the variable to fetch dataset and operators.
method = "DPIR"
dataset_name = "set3c"
img_size = 256 if torch.cuda.is_available() else 32
val_transform = transforms.Compose(
    [transforms.CenterCrop(img_size), transforms.ToTensor()]
)

# Generate a motion blur operator.
kernel_index = 1  # which kernel to chose among the 8 motion kernels from 'Levin09.mat'
kernel_torch = load_degradation(
    "Levin09.npy", DEG_DIR / "kernels", kernel_index=kernel_index
)
kernel_torch = kernel_torch.unsqueeze(0).unsqueeze(
    0
)  # add batch and channel dimensions
dataset = load_dataset(dataset_name, ORIGINAL_DATA_DIR, transform=val_transform)


# %%
# Generate a dataset of blurred images and load it.
# --------------------------------------------------------------------------------
# We use the BlurFFT class from the physics module to generate a dataset of blurred images.


noise_level_img = 0.03  # Gaussian Noise standard deviation for the degradation
n_channels = 3  # 3 for color images, 1 for gray-scale images
p = dinv.physics.BlurFFT(
    img_size=(n_channels, img_size, img_size),
    filter=kernel_torch,
    device=device,
    noise_model=dinv.physics.GaussianNoise(sigma=noise_level_img),
)

# Use parallel dataloader if using a GPU to fasten training,
# otherwise, as all computes are on CPU, use synchronous data loading.
num_workers = 4 if torch.cuda.is_available() else 0

n_images_max = 3  # Maximal number of images to restore from the input dataset
# Generate a dataset in a HDF5 folder in "{dir}/dinv_dataset0.h5'" and load it.
operation = "deblur"
measurement_dir = DATA_DIR / dataset_name / operation
dinv_dataset_path = dinv.datasets.generate_dataset(
    train_dataset=dataset,
    test_dataset=None,
    physics=p,
    device=device,
    save_dir=measurement_dir,
    train_datapoints=n_images_max,
    num_workers=num_workers,
)

batch_size = 1  # batch size for testing
dataset = dinv.datasets.HDF5Dataset(path=dinv_dataset_path, train=True)

# %%
# Set up the DPIR algorithm to solve the inverse problem.
# --------------------------------------------------------------------------------
# This method is based on half-quadratic splitting (HQS).
# The algorithm alternates between a denoising step and a data fidelity step, where
# the denoising step is performed by a pretrained denoiser :class:`deepinv.models.DRUNet`.

# Logging parameters
verbose = True
plot_metrics = True  # compute performance and convergence metrics along the algorithm, curved saved in RESULTS_DIR

# load specific parameters for DPIR
lamb, sigma_denoiser, stepsize, max_iter = get_DPIR_params(noise_level_img)
params_algo = {"stepsize": stepsize, "g_param": sigma_denoiser, "lambda": lamb}
early_stop = False  # Do not stop algorithm with convergence criteria

# Select the data fidelity term
data_fidelity = L2()

# Specify the denoising prior
model_spec = {  # specifies the parameters of the DRUNet model
    "name": "drunet",
    "args": {
        "in_channels": n_channels,
        "out_channels": n_channels,
        "pretrained": "download",
        "train": False,
        "device": device,
    },
}
# The prior g needs to be a dictionary with specified "g" and/or proximal operator "prox_g" and/or gradient "grad_g".
# For Plug-an-Play image restoration, the denoiser replaces "prox_g".
prior = {"prox_g": Denoiser(model_spec)}

# instantiate the algorithm class to solve the IP problem.
model = optim_builder(
    algo_name="HQS",
    prior=prior,
    data_fidelity=data_fidelity,
    early_stop=early_stop,
    max_iter=max_iter,
    verbose=verbose,
    params_algo=params_algo,
    return_metrics=plot_metrics,
)

# %%
# Evaluate the model on the problem.
# --------------------------------------------------------------------
# The test function evaluates the model on the test dataset and computes the metrics.

wandb_vis = False  # plot curves and images in Weight&Bias
plot_images = True  # plot results
save_images = True  # save images in RESULTS_DIR

dataloader = DataLoader(
    dataset, batch_size=batch_size, num_workers=num_workers, shuffle=False
)

test(
    model=model,
    test_dataloader=dataloader,
    physics=p,
    device=device,
    plot_images=plot_images,
    save_images=save_images,
    save_folder=RESULTS_DIR / method / operation / dataset_name,
    plot_metrics=plot_metrics,
    verbose=verbose,
    wandb_vis=wandb_vis,
)