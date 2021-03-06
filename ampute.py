"""Transformer for generating multivariate missingness in complete datasets"""
# Author: Rianne Schouten <riannemargarethaschouten@gmail.com>
# Co-Author: Davina Zamanzadeh <davzaman@gmail.com>

from typing import Callable, Tuple, Union
import logging
import numpy as np
import pandas as pd
import itertools
from pandas import DataFrame
from sklearn.base import TransformerMixin
from scipy import stats
from math import isclose


# Local
from utils import (
    ArrayLike,
    Matrix,
    isin,
    isnan,
    is_numeric,
    enforce_numeric,
    setup_logging,
    missingness_profile,
    sigmoid_scores,
    standardize_uppercase,
)

# TODO: Odds

# TODO: Add fit and transform separately


class MultivariateAmputation(TransformerMixin):
    """Generating multivariate missingness patterns in complete datasets
    n = number of samples.
    m = number of features/vars.
    k = number of patterns.
    Parameters: <param name> : <type/shape> : <default value>
    ----------
    complete_data : matrix with shape (n, m)
        Dataset with no missing values for vars involved in amputation.
        n rows (samples) and m columns (features).
        Values involved in amputation should be numeric, or will be forced.
        Categorical variables should have been transformed to dummies.
    prop : float [0,1] : 0.5
        Proportion of missingness as a decimal or percent.
    patterns : indicator matrix shape (k, m) : square matrix (1 var missing per pattern)
        Specifying observed(1)/missing(0) vars per pattern.
        Each row is 1 pattern (for k total patterns) (minimum 1 pattern).
        Number of patterns is theoretically unlimited,
            but too many will send the data subset size to 0.
    freq : float or array of length k : uniform frequency across patterns
        Relative frequency of each pattern, should sum to 1.
        If one specified, it will be replicated k times.
        For example (k = 3 patterns), freq := [0.4, 0.4, 0.2] =>
            of all samples with missing values,
            40% should have pattern 1, 40% pattern 2. and 2% pattern 3.
    weights : matrix with shape (k, m)
            : MCAR: all 0's, MAR: observed vars weight 1, MNAR: missing vars weight 1.
        Weight matrix specifying size of effect of var on missing vars.
        - negative (decrease effect)
        - 0 (no role in missingness)
        - positive (increase effect).
        Score for sample i in group k = innerproduct(weights[k], sample[i]).
        Within each pattern, the relative size of the values are of importance,
            therefore standardization data for computing scores is important.
    std : boolean : True
        Whether or not to standardize data before computing scores.
        Don't standardize if passing both train and test (prevent leaking).
    mechanism: string or array of length k : MAR
        Specify a mechanism per pattern.
        Choices: [MCAR, MAR, MNAR], case insensitive.
        If one specified, it will be replicated k times.
    types : string or array of length k : RIGHT
        Specify a logit cutoff per pattern.
        Choices: [RIGHT, LEFT, MID, TAIL], case insensitive.
        Dictates a [high, low, average, extreme] score
            (respectively) has a high probability of amputation.
        If one specified, it will be replicated k times.
    lower_range : float : -3
        Lower limit in range to search for b, the horizontal shift
        of the inputs to the sigmoid function in order to assign
        a probability for a value to be missing.
    upper_range : float : 3
        Upper limit in range to search for b, the horizontal shift
        of the inputs to the sigmoid function in order to assign
        a probability for a value to be missing.
    max_dif_with_target : float : 0.001
        The allowable error between the desired percent missing data (prop)
        and and calculated joint missing probability after assigning a
        probability for values to be missing.
    max_iter : integer : 100
        Max number of iterations for binary search when searching for b,
        the horizontal shift of the inputs (weighted sum scores) to the
        sigmoid function.
    score_to_probability_func : fn takes an array, shift amount, and type of cutoff
                              : sigmoid
        Function converts standardized weighted scores for each sample (in a
        data subset corresponding to pattern k) to probability of missingness
        for each sample according to a cutoff type in self.types, i.e.
            a vector of values [0, 1] that don't have to sum to 1.
        - shift amount is an additional shift constant that we find via binary search to
        ensure the joint missingness probabilities of multiple vars makes sense.
        ! Note any function that can take a raw value and map it to [0, 1] will work
            in general, though might not behave according to a cutoff unless tested.
        # TODO: in validation test if the function passed will work approximately?
    Attributes
    ----------
    incomplete_data :  matrix with shape (n, m)
        Dataset with missing values.
    Notes
    -----
    Something on difference ampute in R and Python
    #TODO: any more detailed explanations
    References
    ----------
    .. [1] Rianne Margaretha Schouten, Peter Lugtig & Gerko Vink (2018).
    Generating missing values for simulation purposes:
        A multivariate amputation procedure.
    Journal of Statistical Computation and Simulation, DOI:
        10.1080/00949655.2018.1491577
    """

    def __init__(
        self,
        prop: float = 0.5,
        patterns: Matrix = None,
        freqs: Union[float, ArrayLike] = None,
        weights: Matrix = None,
        std: bool = True,
        mechanisms: Union[str, ArrayLike] = None,
        types: Union[str, ArrayLike] = None,
        lower_range: float = -3,
        upper_range: float = 3,
        max_dif_with_target: float = 0.001,
        max_iter: int = 100,
        score_to_probability_func: Callable[[ArrayLike], ArrayLike] = sigmoid_scores,
    ):

        self.prop = prop
        self.patterns = patterns
        self.freqs = freqs
        self.weights = weights
        self.std = std
        self.mechanisms = mechanisms
        self.types = types
        self.lower_range = lower_range
        self.upper_range = upper_range
        self.max_dif_with_target = max_dif_with_target
        self.max_iter = max_iter
        self.score_to_probability_func = score_to_probability_func

        setup_logging()

    def _binary_search(
        self, wss_standardized: ArrayLike, pattern_ind: int
    ) -> Tuple[float, Matrix]:
        """
        Search for the appropriate shift/transformation to the scores before passing
            through the self.probability_function to result in the desired missingness
            proportion.  e.g. raw wss will mask 17% of samples in pattern k but you want
            40% missing.
        """

        b = 0
        counter = 0
        lower_range = self.lower_range
        upper_range = self.upper_range

        probs_matrix = None

        # start binary search with a maximum amount of tries of max_iter
        while counter < self.max_iter:
            counter += 1

            # in every iteration, the new b is the mid of the lower and upper range
            # the lower and upper range are updated at the end of each iteration
            b = lower_range + (upper_range - lower_range) / 2
            if counter == self.max_iter:
                break

            # calculate the expected missingness proportion
            # depends on the logit cutoff type, the sum scores and b
            probs_matrix = self.score_to_probability_func(
                wss_standardized, b, self.types[pattern_ind]
            )
            current_prop = np.mean(probs_matrix)

            # if the expected proportion is close to the target, break
            # the maximum difference can be specified
            # if max_dif_with_target is 0.001, the proportion differs with max 0.1%
            if np.absolute(current_prop - self.prop) < self.max_dif_with_target:
                break

            # if we have not reached the desired proportion
            # we adjust either the upper or lower range
            # this way of adjusting works for self.types[i] = 'RIGHT'
            # need to check for the other types
            # in the next iteration, a new b is then calculated and used
            if (current_prop - self.prop) > 0:
                upper_range = b
            else:
                lower_range = b

        return b, probs_matrix

    def _choose_probabilities(self, wss: ArrayLike, pattern_index: int) -> Matrix:
        """
        Assigns missingness probabilities for each sample in the data subset
            corresponding to pattern k (pattern_index) using the standardized wss.
        This is later thresholded to use to decide whether or not to apply pattern k
        to sample i.
        """
        # when wss contains merely zeros, the mechanism is
        # 1. MCAR: each case has an equal probability of becoming missing
        # 2. MAR with binary variables
        # Therefore we just use uniform probability of missing per var using self.freqs
        if np.all(wss == 0):
            probs = np.repeat(self.freqs[pattern_index], len(wss))
        else:  # else we calculate the probabilities based on the wss
            # standardize wss
            wss_standardized = stats.zscore(wss)
            # calculate the size of b for the desired missingness proportion
            b, probs_matrix = self._binary_search(wss_standardized, pattern_index)
            probs = np.squeeze(np.asarray(probs_matrix))

        return probs

    def _calculate_sumscores(self, data_group: Matrix, pattern_ind: int) -> ArrayLike:
        """
        Creates a vector of weighted sum score for each sample in the data subset
        corresponding to pattern k by computing the inner product of
            self.weights and the raw values of the samples in that subset.
        This is later converted to a probability to be thresholded on to decide
            whether or not to apply pattern k to sample i in the data subset.
        """

        # transform only vars involved in amputation to numeric to compute weights
        # does not transform the original datset
        logging.info(
            "Enforcing data to be numeric since calculation of weights"
            " requires numeric data."
        )
        #print(data_group)
        data_group = enforce_numeric(data_group)
        #print(data_group)
        # standardize data or not
        if self.std:
            data_group = stats.zscore(data_group)
            #data_group = data_group.fillna(0)

        # calculate sum scores
        # in case of MCAR, weights[i, ] contains merely zeros and wss are merely zeros
        # in case of MAR, MNAR, the mechanisms is determined by the weights
        #print(np.multiply(np.array(data_group),self.weights[pattern_ind, :].T))
        #print(self.weights[pattern_ind, :].T)
        #wss = np.dot(data_group, self.weights[pattern_ind, :].T)
        #print(wss)
        wss = sum(np.multiply(np.array(data_group.fillna(0)), self.weights[pattern_ind, :].T).T)
        return wss

    def _set_defaults(self):
        """
        Set defaults for args, assuming patterns has been initialized.
        Most of the defaults rely on info from patterns.
        Will adjust vars:
            change % to decimal, repeat for all patterns,
            standardize strings to uppercase force lists to np arrays, etc.)
        """
        # check for prop that makes sense, since we validate after setting defaults
        if self.prop > 1 and self.prop <= 100:
            logging.info(
                "Detected proportion of missingness to be percentage,"
                " converting to decimal."
            )
            self.prop /= 100

        # RELIES ON: patterns
        if self.freqs is None:
            logging.info("No freq passed, assigning uniform frequency across patterns.")
            self.freqs = np.repeat(1 / self.num_patterns, self.num_patterns)
        elif isinstance(self.freqs, float) or isinstance(self.freqs, int):
            logging.info("One frequency passed, assigning to every pattern.")
            self.freqs = np.repeat(self.freqs, self.num_patterns)
        # TODO : chop off extras?
        elif len(self.freqs) == 1:
            logging.info("One frequency passed, assigning to every pattern.")
            self.freqs = np.repeat(self.freqs[0], self.num_patterns)
        else:  # force numpy
            self.freqs = np.array(self.freqs)
        # TODO : chop off extras?
        # TODO: recalculate frequencies to sum to 1?

        # RELIES ON: patterns
        if self.mechanisms is None:
            logging.info("No mechanisms passed, assuming MAR for every pattern.")
            self.mechanisms = np.repeat("MAR", self.num_patterns)
        elif isinstance(self.mechanisms, str):
            logging.info("One mechanism passed, assigning to every pattern.")
            self.mechanisms = np.repeat(
                standardize_uppercase(self.mechanisms), self.num_patterns
            )
        elif len(self.mechanisms) == 1:  # repeat same mechanism for all vars
            logging.info("One mechanism passed, assigning to every pattern.")
            self.mechanisms = np.repeat(
                standardize_uppercase(self.mechanisms[0]), self.num_patterns
            )
        else:  # nothing else to adjust, just standardize to upper case
            self.mechanisms = np.array(
                list(map(standardize_uppercase, self.mechanisms))
            )
        # assertion here instead of validate_args because weights depends on this.
        assert (
            len(self.mechanisms) == self.num_patterns
        ), "Must specify a mechanism per pattern, but they do not match."

        # RELIES ON: patterns
        if self.types is None:
            logging.info(
                "No amputation type passed, assuming RIGHT amputation."
                " Large scores are assigned high probability to be amputed."
            )
            self.types = np.repeat("RIGHT", self.num_patterns)
        elif isinstance(self.types, str):
            logging.info("One type passed, assigning to every pattern.")
            self.types = np.repeat(standardize_uppercase(self.types), self.num_patterns)
        elif len(self.types) == 1:
            logging.info("One type passed, assigning to every pattern.")
            self.types = np.repeat(
                standardize_uppercase(self.types[0]), self.num_patterns
            )
        else:  # nothing else to adjust, just standardize to upper case
            self.types = np.array(list(map(standardize_uppercase, self.types)))

        # RELIES ON: patterns, mechanisms
        if self.weights is None:
            logging.info(
                "No weights passed."
                " MCAR: weights are all 0s."
                " MAR: all observed vars have weight 1."
                " MNAR: all missing vars have weight 1."
            )
            self.weights = np.zeros(shape=(self.num_patterns, self.num_features))
            self.weights[self.mechanisms == "MAR"] = self.patterns[
                self.mechanisms == "MAR",
            ]
            # note that non-observed is given a value 0 in patterns
            self.weights[self.mechanisms == "MNAR"] = (
                1 - self.patterns[self.mechanisms == "MNAR"]
            )

    def _validate_args(self):
        """
        Validates remainined constructor args after having set defaults.
        Only makes assertions, assuming everything is initialized.
        """
        ####################
        #     PATTERNS     #
        ####################
        # axis=None reduces all axes for both pandas and numpy
        assert isin(self.patterns, [0, 1]).all(
            axis=None
        ), "Patterns can only contain 0's and 1's."
        assert not ((self.patterns == 1).all(axis=None)), (
            "Patterns cannot be all 1's."
            " A pattern with all 1's results in no amputation."
        )
        if isin(self.mechanisms, "MAR").any(axis=0):
            assert not (self.patterns[self.mechanisms == "MAR"] == 0).all(axis=None), (
                "Patterns cannot be all 0's if specifying MAR."
                " A pattern with all 0's results in all vars missing."
            )

        ##################
        #      PROP      #
        ##################
        assert self.prop >= 0 and self.prop <= 100, (
            "Proportion of missingness should be a value between 0 and 1"
            " (for a proportion) or between 1 and 100 (for a percentage)"
        )

        ###################
        #   FREQUENCIES   #
        ###################
        assert len(self.freqs) == self.num_patterns, (
            "There should be a frequency of missingness for every pattern,"
            f" but there are only {len(self.freqs)} frequencies specified,"
            f" and {self.num_patterns} patterns specified from `patterns`."
        )
        assert (self.freqs >= 0).all() and (
            self.freqs <= 1
        ).all(), "Frequencies must be between 0 and 1 inclusive."
        # there's imprecision in float, so it might be 0.9999999
        assert isclose(sum(self.freqs), 1), "Frequencies should sum to 1."

        ##################
        #   MECHANISMS   #
        ##################
        assert (
            len(self.mechanisms) == self.num_patterns
        ), "Must specify a mechanism per pattern, but they do not match."
        assert isin(
            self.mechanisms, ["MCAR", "MAR", "MNAR"]
        ).all(), "Mechanisms specified must be one of ['MCAR', 'MAR', 'MNAR']."

        #################
        #    WEIGHTS    #
        #################
        assert (
            self.weights.shape == self.patterns.shape
        ), "Weights passed must match dimensions of patterns passed."
        assert (self.weights[self.mechanisms == "MCAR"] == 0).all(
            axis=None
        ), "Patterns with MCAR should have weights of all 0's."

        #################
        #     TYPES     #
        #################
        assert (
            len(self.types) == self.num_patterns
        ), "Types, mechs, and freqs must all be the same dimension (# patterns)."
        assert isin(
            self.types, ["RIGHT", "LEFT", "MID", "TAIL"]
        ).all(), "Types can only be one of ['right', 'left', 'mid', 'tail']."

    def _validate_input(self, X: Matrix) -> Matrix:
        """
        Validates input data with given arguments to amputer.
        Will modify the dataset to comply if possible, while giving warnings.
        """
        # This must come first so we can check patterns
        assert X is not None, "No dataset passed, cannot be None."
        assert len(X.shape) == 2, "Dataset must be 2 dimensional."

        ##################
        #    PATTERNS    #
        ##################
        if self.patterns is None:
            logging.info("No patterns passed, assuming missingness on each variable.")
            self.patterns = 1 - np.identity(n=X.shape[1])
        else:
            assert (
                len(self.patterns.shape) == 2
            ), "If a pattern is provided, it must be 2 dimensional."
        assert self.patterns.shape[1] == X.shape[1], (
            "Each pattern should specify weights for each feature."
            " The number of entries for each pattern does not match the"
            " number of features in the dataset."
        )

        # bookkeeping vars for readability
        self.num_patterns = self.patterns.shape[0]
        self.num_features = self.patterns.shape[1]

        # defaults for the rest of the args (depends on patterns being initialized)
        self._set_defaults()
        self._validate_args()

        # vars involved in amputation have scores computed and need to be
        #   complete and numeric
        # A var (column) is involved if for any pattern (row) it has a weight.
        vars_involved_in_ampute = (self.weights != 0).any(axis=0)

        ##################
        #      DATA      #
        ##################
        assert X.shape[1] > 1, "Dataset passed must contain at least two columns."
        # enforce numpy just for checking
        X_check = X.values if isinstance(X, DataFrame) else X
        assert not isnan(
            X_check[:, vars_involved_in_ampute]
        ).any(), "Features involved in amputation must be complete, but contains NaNs."
        if not is_numeric(X_check[:, vars_involved_in_ampute]):
            logging.warn(
                "Features involved in amputation found to be non-numeric."
                " They will be forced to numeric upon calculating sum scores."
            )

        return X

    def fit_transform(self, X: Matrix) -> Matrix:
        """Fits amputer on complete data X and returns the incomplete data X
        Parameters
        ----------
        X : matrix of shape (n_samples, m_features)
            Complete input data, where "n_samples" is the number of samples and
            "m_features" is the number of features.
        Returns
        -------
        X_incomplete : matrix of shape (n_samples, m_features)
        """
        print('dataframe gotten to here!')
        cols = list(X.columns)

        X = np.array(X)
        # sets defaults, adjusts vars, and runs checks

        X = self._validate_input(X)

        # split complete_data in groups
        # the number of groups is defined by the number of patterns
        # we know the number of patterns by the number of rows of self.patterns
        num_samples = X.shape[0]
        shape_y = len([x for x in cols if '_end_' not in str(x)])
        #print(X.shape[0],shape_y)
        X_incomplete = np.zeros((X.shape[0],shape_y))
        X_indices = np.arange(num_samples)
        assigned_group_number = np.random.choice(
            a=self.num_patterns, size=num_samples, p=self.freqs
        )

        # start a loop over each pattern
        for pattern_ind in range(self.num_patterns):
            # assign cases to the group
            group_indices = X_indices[assigned_group_number == pattern_ind]
            pattern = np.squeeze(np.asarray(self.patterns[pattern_ind, :]))
            data_group = X[group_indices]

            backtodf = pd.DataFrame(data_group, columns=cols)
            begins = list(backtodf.filter(regex='^_start_').columns)
            ends = list(backtodf.filter(regex='^_end_').columns)

            stripped = backtodf.drop(begins+ends, axis=1)

            level_combinations = list(itertools.product([0,1], repeat=len(begins)))
            levels = pd.DataFrame([begins, ends])

            wss_stash = []
            for item in range(len(level_combinations)):
                combo = []
                for i in range(len(level_combinations[0])):
                    combo.append(levels[i].loc[level_combinations[item][i]])

                group = stripped.join(backtodf[combo])
                for i in range(len(level_combinations[0])):
                    group['dummy_'+str(i)] = 0
            # calculate weighted sum scores for each sample in the group

                wss_stash.append(self._calculate_sumscores(group, pattern_ind))

            #print(wss_stash)
            wss = sum(wss_stash)/len(wss_stash)
            # define candidate probabilities in group
            probs = self._choose_probabilities(wss, pattern_ind)
            # apply probabilities and choose cases
            chosen_candidates = np.random.binomial(
                n=1, size=data_group.shape[0], p=probs
            )
            # apply missing data pattern
            chosen_indices = group_indices[chosen_candidates == 1]
            diff = len(pattern)-shape_y
            pattern = pattern[0:-diff]
            if sum(pattern) != len(pattern):
                X_incomplete[chosen_indices, pattern == 0] = np.nan

        missingness_profile(X_incomplete)
        return X_incomplete