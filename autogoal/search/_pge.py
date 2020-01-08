import scipy

from typing import Mapping, Optional, Dict
from autogoal.grammar import Sampler
from ._base import SearchAlgorithm


class ModelSampler(Sampler):
    def __init__(self, model: Dict = None, **kwargs):
        super().__init__(**kwargs)
        self._model: Dict = model or {}
        self._updates: Dict = {}

    @property
    def model(self):
        return self._model

    @property
    def updates(self):
        return self._updates

    def _get_model_params(self, handle, default):
        if handle in self._model:
            return self._model[handle]
        else:
            self._model[handle] = default
            return default

    def _register_update(self, handle, result):
        if handle not in self._updates:
            self._updates[handle] = []

        self._updates[handle].append(result)
        return result

    def _clamp(self, x, a, b):
        if x < a:
            return a
        if x > b:
            return b
        return x

    def choice(self, options, handle=None):
        if handle is not None:
            return self._sample_categorical(handle, options)

        weights = [self._get_model_params(option, 1) for option in options]
        idx = self.rand.choices(range(len(options)), weights=weights, k=1)[0]
        option = options[idx]
        self._register_update(option, 1)
        return option

    def _sample_discrete(self, handle, min, max):
        if handle is None:
            return super()._sample_discrete(handle, min, max)

        mean, stdev = self._get_model_params(handle, ((min + max) / 2, (max - min)))
        value = self._clamp(int(self.rand.gauss(mean, stdev)), min, max)
        return self._register_update(handle, value)

    def _sample_continuous(self, handle, min, max):
        if handle is None:
            return super()._sample_continuous(handle, min, max)

        mean, stdev = self._get_model_params(handle, ((min + max) / 2, (max - min)))
        value = self._clamp(self.rand.gauss(mean, stdev), min, max)
        return self._register_update(handle, value)

    def _sample_boolean(self, handle):
        if handle is None:
            return super()._sample_boolean(handle)

        p = self._get_model_params(handle, (0.5,))[0]
        value = self.rand.uniform(0, 1) < p
        return self._register_update(handle, value)

    def _sample_categorical(self, handle, options):
        if handle is None:
            return super()._sample_categorical(handle, options)

        weights = self._get_model_params(handle, [1 for _ in options])
        idx = self.rand.choices(range(len(options)), weights=weights, k=1)[0]
        return options[self._register_update(handle, idx)]


def update_model(model, updates, alpha: float = 1):
    new_model = {}

    for handle, params in model.items():
        upd = updates.get(handle)

        if upd is None:
            new_model[handle] = params
            continue

        # TODO: refactor to a more Object Oriented way
        if isinstance(params, (float, int)):
            # float or int means a single un-normalized weight
            new_model[handle] = params + alpha * sum(upd)
        elif isinstance(params, list):
            # a list means a (potentially un-normalized) distribution over categories
            new_model[handle] = list(params)
            for i in upd:
                new_model[handle][i] += alpha
        elif isinstance(params, tuple):
            # a tuple means specific distribution parameters, like mean and stdev
            if len(params) == 2:
                mean, stdev = params
                new_mean = scipy.mean(upd)
                new_stdev = scipy.std(upd)
                new_model[handle] = (
                    mean * (1 - alpha) + new_mean * alpha,
                    stdev * (1 - alpha) + new_stdev * alpha,
                )
            elif len(params) == 1:
                p = params[0]
                new_p = upd.count(True)
                new_model[handle] = (p * (1 - alpha) + new_p * alpha,)
            else:
                raise ValueError("Unrecognized params %r" % params)
        else:
            raise ValueError("Unrecognized params %r" % params)

    return new_model


class PESearch(SearchAlgorithm):
    def __init__(
        self,
        *args,
        pop_size: int = 100,
        learning_factor: float = 0.05,
        selection: float = 0.2,
        random_state: Optional[int] = None,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self._pop_size = pop_size
        self._learning_factor = learning_factor
        self._selection = selection
        self._model: Dict = {}

    def _run_one_generation(self):
        self._samplers = []

        for _ in range(self._pop_size):
            sampler = ModelSampler(self._model)
            self._samplers.append(sampler)
            yield self._grammar.sample(sampler=sampler)

    @staticmethod
    def _indices(l):
        # taken from https://stackoverflow.com/questions/6422700
        def argsort(l):
            return sorted(range(len(l)), key=l.__getitem__)

        return argsort(argsort(l))

    def _finish_generation(self, fns):
        # taken from https://stackoverflow.com/questions/6422700
        indices = self._indices(fns)
        to_select = int(self._selection * len(fns))

        if to_select == 0:
            to_select = len(fns)

        if self._maximize:
            to_select = len(fns) - to_select
            selected = [self._samplers[i] for i in range(len(fns)) if indices[i] >= to_select]
        else:
            selected = [self._samplers[i] for i in range(len(fns)) if indices[i] < to_select]

        model = selected[0].model

        for sampler in selected:
            model = update_model(model, sampler.updates, self._learning_factor)

        # TODO: implement propper loging
        # import pprint
        # pprint.pprint(model)

        self._model = model