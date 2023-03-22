import torch
from torch import nn
from .utils import register_to_module, turn_to_tuple, make_args, invoke_with_cond
from abc import abstractmethod, ABC
import torch.distributions as D


# come up with a better name
# here we assume that conditioning of the joint distribution can occur
# by conditioning the prior
class Joint(nn.Module):
    def __init__(self, prior, conditional):
        super().__init__()
        self.prior = prior
        self.conditional = conditional
        self.split = self.prior.n_rvs
        self.n_rvs = self.prior.n_rvs + self.conditional.n_rvs

    def log_prob(self, *obs, cond=None):
        # TODO: maybe just use self.prior.n_rvs
        x, y = obs[: self.split], obs[self.split :]
        return self.prior(*x, cond=cond) + self.conditional(*y, cond=x)

    def forward(self, *obs, cond=None):
        return self.log_prob(*obs, cond=cond)

    def sample(self, sample_shape=torch.Size([]), cond=None):
        x_samples = self.prior.sample(sample_shape=sample_shape, cond=cond)
        y_samples = self.conditional.sample(cond=x_samples)
        return turn_to_tuple(x_samples) + turn_to_tuple(y_samples)

    def rsample(self, sample_shape=torch.Size([]), cond=None):
        x_samples = self.prior.rsample(sample_shape=sample_shape, cond=cond)
        y_samples = self.conditional.rsample(cond=x_samples)
        return turn_to_tuple(x_samples) + turn_to_tuple(y_samples)


# class DeltaDistribution(nn.Module):
#     def __init__(self, value):
#         self.value = value

#     def log_prob(self, obs, cond=None):
#         # TODO: write to deal with more than one rvs
#         return torch.log(self.prob(obs, cond=cond))

#     def prob(self, obs, cond=None):
#         # TODO: write to deal with more than one rvs
#         return torch.where(
#             torch.equal(obs, parse_attr(self.value, cond=cond)), 0, 1
#         ).to(obs.device)

#     def sample(self, sample_shape=torch.size([]), cond=None):


class TrainableDistribution(nn.Module, ABC):
    """
    Here we are providing the proper abstract base class for the
    TrainableDistribution. However, at the moment, this is not used
    anywhere and only serving to indicate what methods must be implemented
    for any object to be considered a proper, trainable distribution.
    """

    @property
    @abstractmethod
    def n_rvs(self):
        """
        Defines the number of random variables that this distribution is
        defined over.
        """
        ...

    @abstractmethod
    def log_prob(self, *obs, cond=None):
        ...

    def forward(self, *obs, cond=None):
        return self.log_prob(*obs, cond=cond)

    @abstractmethod
    def sample(self, sample_shape=torch.Size([]), cond=None):
        ...

    @abstractmethod
    def rsample(self, sample_shape=torch.Size([]), cond=None):
        ...


class TrainableDistributionAdapter(nn.Module):
    n_rvs = 1

    def __init__(self, distribution_class, *dist_args, _parameters=None, **dist_kwargs):
        super().__init__()
        self.distribution_class = distribution_class
        self.param_counts = len(dist_args)
        self.param_keys = list(dist_kwargs.keys())

        for pos, val in enumerate(dist_args):
            # setattr(self, f'_arg{pos}', val)
            register_to_module(self, f"_arg{pos}", val)
        for key, val in dist_kwargs.items():
            register_to_module(self, key, val)

        # The alternative interface to allow for a single module to
        # flexibly output multiple parameters for the distribution
        # at the moment, the module is expected to output a dictionary
        # of parameters
        if _parameters is not None:
            self.parameter_generator = _parameters

    def distribution(self, cond=None):
        cond = turn_to_tuple(cond)

        dist_args = tuple(
            invoke_with_cond(getattr(self, f"_arg{pos}"), cond=cond)
            for pos in range(self.param_counts)
        )
        dist_kwargs = {
            k: invoke_with_cond(getattr(self, k), cond=cond) for k in self.param_keys
        }

        # TODO: consider flipping the order of this with
        # init specified parameters
        if hasattr(self, "parameter_generator"):
            dist_args, dist_kwargs = make_args(
                self.parameter_generator(*cond), *dist_args, **dist_kwargs
            )

        return self.distribution_class(*dist_args, **dist_kwargs)

    def log_prob(self, *obs, cond=None):
        return self.distribution(cond=cond).log_prob(*obs)

    def forward(self, *obs, cond=None):
        return self.log_prob(*obs, cond=cond)

    def sample(self, sample_shape=torch.Size([]), cond=None):
        return self.distribution(cond=cond).sample(sample_shape=sample_shape)

    def rsample(self, sample_shape=torch.Size([]), cond=None):
        return self.distribution(cond=cond).rsample(sample_shape=sample_shape)


def wrap_with_indep(distribution_class, event_dims=1):
    """
    Wrap the construction of the target distribution `distr_class` with
    D.Independent. The returned function can be used as if it is
    a constructor for an indepdenent version of the distribution
    """

    def indep_distr(*args, **kwargs):
        return D.Independent(distribution_class(*args, **kwargs), event_dims)

    return indep_distr


class WrappedTrainableDistribution(nn.Module):
    def __init__(self, trainable_distribution=None):
        super().__init__()
        self.trainable_distribution = trainable_distribution

    def forward(self, *obs, cond=None):
        return self.trainable_distribution(*obs, cond=cond)

    def log_prob(self, *obs, cond=None):
        return self.trainable_distribution.log_prob(*obs, cond=cond)

    def sample(self, sample_shape=torch.Size([]), cond=None):
        return self.trainable_distribution.sample(sample_shape=sample_shape, cond=cond)

    def rsample(self, sample_shape=torch.Size([]), cond=None):
        return self.trainable_distribution.rsample(sample_shape=sample_shape, cond=cond)


class IndependentNormal(WrappedTrainableDistribution):
    def __init__(self, loc, scale, event_dims=1):
        super().__init__()
        self.trainable_distribution = TrainableDistributionAdapter(
            wrap_with_indep(D.Normal, event_dims=event_dims), loc=loc, scale=scale
        )
