"""Investment constraints.

Constraints on investements ensure that investements match some given criteria. For
instance, the constraints could ensure that only so much of a new asset can be built
every year.

Functions to compute constraints should be registered via the decorator
:py:func:`register_constraints`. This registration step makes it possible for
constraints to be declared in the TOML file.
"""

from enum import Enum, auto
from typing import (
    Callable,
    Optional,
    List,
    Mapping,
    MutableMapping,
    Sequence,
    Text,
    Union,
    Tuple,
    cast,
)

from xarray import DataArray, Dataset

from muse.registration import registrator

CAPACITY_DIMS = "asset", "replacement", "region"
"""Default dimensions for capacity decision variables."""
PRODUCT_DIMS = "commodity", "timeslice", "region"
"""Default dimensions for product decision variables."""


class ConstraintKind(Enum):
    EQUALITY = auto()
    UPPER_BOUND = auto()
    LOWER_BOUND = auto()


Constraint = Dataset
"""An investment constraint :math:`A * x ~ b`

Where :math:`~` is one of :math:`=,\\leq,\\geq`.

A constraint should contain a data-array `b` corresponding to right-hand-side vector
of the contraint. It should also contain a data-array `capacity` corresponding to the
left-hand-side matrix operator which will be applied to the capacity-related decision
variables.  It should contain a similar matrix `production` corresponding to
the left-hand-side matrix operator which will be applied to teh production-related
decision variables. Should any of these three objects be missing, they default to the
scalar 0. Finally, the constraint should contain an attribute `kind` of type
:py:class:`ConstraintKind` defining the operation. If it is missing, it defaults to an
upper bound constraint.
"""


CONSTRAINT_SIGNATURE = Callable[[Dataset, DataArray, Dataset, Dataset], Constraint]
"""Basic signature for functions producing constraints."""
CONSTRAINTS: MutableMapping[Text, CONSTRAINT_SIGNATURE] = {}
"""Registry of constraint functions."""


@registrator(registry=CONSTRAINTS)
def register_constraints(function: CONSTRAINT_SIGNATURE) -> CONSTRAINT_SIGNATURE:
    from functools import wraps

    @wraps(function)
    def decorated(
        assets: Dataset,
        search_space: DataArray,
        market: Dataset,
        technologies: Dataset,
        **kwargs,
    ) -> Constraint:
        """Computes and standardizes a constraint."""
        constraint = function(  # type: ignore
            assets, search_space, market, technologies, **kwargs
        )
        if "kind" not in constraint.attrs:
            constraint.attrs["kind"] = ConstraintKind.UPPER_BOUND
        if (
            "capacity" not in constraint.data_vars
            and "production" not in constraint.data_vars
        ):
            raise RuntimeError("Invalid constraint format")
        if "capacity" not in constraint.data_vars:
            constraint["capacity"] = 0
        if "production" not in constraint.data_vars:
            constraint["production"] = 0
        if "b" not in constraint.data_vars:
            constraint["b"] = 0

        return constraint

    return decorated


def factory(
    settings: Union[Text, Mapping, Sequence[Mapping]] = "max_capacity_expansion"
) -> Callable:
    if isinstance(settings, Text):
        names = [settings]
        params: List[Mapping] = [{}]
    elif isinstance(settings, Mapping):
        names = [settings["name"]]
        params = [{k: v for k, v in settings.items() if k != "name"}]

    def constraints(
        assets: Dataset,
        search_space: DataArray,
        technologies: Dataset,
        year: int,
        **kwargs,
    ) -> List[Constraint]:
        return [
            CONSTRAINTS[name](  # type: ignore
                assets, search_space, technologies, year=year, **{**param, **kwargs}
            )
            for name, param in zip(names, params)
        ]

    return constraints


@register_constraints
def max_capacity_expansion(
    assets: Dataset,
    search_space: DataArray,
    market: Dataset,
    technologies: Dataset,
    forecast: int = 5,
    interpolation: Text = "linear",
) -> Constraint:
    r"""Max-capacity addition, max-capacity growth, and capacity limits constraints.

    Limits by how much the capacity of each technology owned by an agent can grow in
    a given year. This is a constraint on the agent's ability to invest in a
    technology.

    Let :math:`L_t^r(y)` be the total capacity limit for a given year, technology,
    and region. :math:`G_t^r(y)` is the maximum growth. And :math:`W_t^r(y)` is
    the maximum additional capacity. :math:`y=y_0` is the current year and
    :math:`y=y_1` is the year marking the end of the investment period.

    Let :math:`\mathcal{A}^{i, r}_{t, \iota}(y)` be the current assets, before
    invesment, and let :math:`\Delta\mathcal{A}^{i,r}_t` be the future investements.
    The the constraint on agent :math:`i` are given as:

    .. math::

        L_t^r(y_0) - \sum_\iota \mathcal{A}^{i, r}_{t, \iota}(y_1)
            \geq \Delta\mathcal{A}^{i,r}_t

        (y_1 - y_0 + 1) G_t^r(y_0) \sum_\iota \mathcal{A}^{i, r}_{t, \iota}(y_0)
            - \sum_\iota \mathcal{A}^{i, r}_{t, \iota}(y_1)
            \geq \Delta\mathcal{A}^{i,r}_t

        (y_1 - y_0)W_t^r(y_0) \geq  \Delta\mathcal{A}^{i,r}_t

    The three constraints are combined into a single one which is returned as the
    maximum capacity expansion, :math:`\Gamma_t^{r, i}`. The maximum capacity
    expansion cannot impose negative investments:
    Maximum capacity addition:

        .. math::

            \Gamma_t^{r, i} \geq 0

    Example:

        >>> from muse import examples
        >>> from muse.constraints import max_capacity_expansion
        >>> res = examples.sector("residential", model="medium")
        >>> technologies = res.technologies
        >>> market = examples.residential_market("medium")
        >>> search_space = examples.search_space("residential", model="medium")
        >>> assets = next(a.assets for a in res.agents if a.category == "retrofit")
        >>> maxcap = max_capacity_expansion(assets, search_space, market, technologies)
    """
    from muse.utilities import filter_input

    year = market.year.min()
    forecast_year = forecast + year

    techs = filter_input(
        technologies[
            ["max_capacity_addition", "max_capacity_growth", "total_capacity_limit"]
        ],
        technology=search_space.replacement,
        year=year,
    )
    assert isinstance(techs, Dataset)

    capacity = (
        assets.capacity.groupby("technology")
        .sum("asset")
        .interp(year=[year, forecast_year], method=interpolation)
        .rename(technology=search_space.replacement.name)
        .reindex_like(search_space.replacement, fill_value=0)
    )

    add_cap = techs.max_capacity_addition * forecast

    limit = techs.total_capacity_limit
    forecasted = capacity.sel(year=forecast_year, drop=True)
    total_cap = (limit - forecasted).clip(min=0).rename("total_cap")

    max_growth = techs.max_capacity_growth
    initial = capacity.sel(year=year, drop=True)
    growth_cap = initial * (max_growth * forecast + 1) - forecasted

    zero_cap = add_cap.where(add_cap < total_cap, total_cap)
    with_growth = zero_cap.where(zero_cap < growth_cap, growth_cap)
    constraint = with_growth.where(initial > 0, zero_cap)
    return Dataset(
        dict(b=constraint, capacity=1), attrs=dict(kind=ConstraintKind.UPPER_BOUND)
    )


@register_constraints
def demand(
    assets: Dataset,
    search_space: DataArray,
    market: Dataset,
    technologies: Dataset,
    forecast: int = 5,
    interpolation: Text = "linear",
) -> Constraint:
    """Constraints production to meet demand.

    Example:

        >>> from muse import examples
        >>> from muse import constraints
        >>> technologies = examples.technodata("residential", model="medium")
        >>> market = examples.residential_market("medium")
        >>> search_space = None # Not used on demand
        >>> assets = None  # not used in demand
        >>> demand = constraints.demand(assets, search_space, market, technologies)
    """
    from muse.commodities import is_enduse

    enduse = technologies.commodity.sel(commodity=is_enduse(technologies.comm_usage))
    b = market.consumption.sel(commodity=market.commodity.isin(enduse)).interp(
        year=market.year.min() + forecast
    )
    return Dataset(dict(b=b, production=1), attrs=dict(kind=ConstraintKind.EQUALITY))


@register_constraints
def max_production(
    assets: Dataset,
    search_space: DataArray,
    market: Dataset,
    technologies: Dataset,
    forecast: int = 5,
    interpolation: Text = "linear",
) -> Constraint:
    """Constructs contraint between capacity and maximum production.

    Constrains the production decision variable by the maximum production for a given
    capacity.

    Example:

        >>> from muse import examples
        >>> from muse.constraints import max_production
        >>> technologies = examples.technodata("residential", model="medium")
        >>> market = examples.residential_market("medium")
        >>> search_space = examples.search_space("residential", "medium")
        >>> assets = None  # not used in max_production
        >>> maxprod = max_production(assets, search_space, market, technologies)
    """
    from xarray import zeros_like, ones_like
    from muse.commodities import is_enduse
    from muse.timeslices import convert_timeslice, QuantityType

    commodities = technologies.commodity.sel(
        commodity=is_enduse(technologies.comm_usage)
    )
    techs = technologies[["fixed_outputs", "utilization_factor"]].sel(
        year=market.year.min(),
        commodity=commodities,
        technology=search_space.replacement,
    )
    capacity = convert_timeslice(
        techs.fixed_outputs * techs.utilization_factor,
        market.timeslice,
        QuantityType.EXTENSIVE,
    ).expand_dims(asset=search_space.asset)
    production = -ones_like(capacity)
    b = zeros_like(production)
    return Dataset(
        dict(capacity=capacity, production=production, b=b),
        attrs=dict(kind=ConstraintKind.UPPER_BOUND),
    )


def lp_costs(technologies: Dataset, costs: DataArray, timeslices: DataArray) -> Dataset:
    """Creates costs for solving with scipy's LP solver.

    Example:

        We can now construct example inputs to the funtion from the sample model. The
        costs will be a matrix where each assets has a candidate replacement technology.

        >>> from muse import examples
        >>> technologies = examples.technodata("residential", model="medium")
        >>> search_space = examples.search_space("residential", model="medium")
        >>> timeslices = examples.sector("residential", model="medium").timeslices
        >>> costs = (
        ...     search_space
        ...     * np.arange(np.prod(search_space.shape)).reshape(search_space.shape)
        ... )

        The function returns the LP vector split along capacity and production
        variables.

        >>> from muse.constraints import lp_costs
        >>> lpcosts = lp_costs(
        ...     technologies.sel(year=2020, region="USA"), costs, timeslices
        ... )
        >>> lpcosts
        <xarray.Dataset>
        Dimensions:      (asset: 4, commodity: 2, replacement: 4, timeslice: 6)
        Coordinates:
          * asset        (asset) object 'estove' 'gasboiler' 'gasstove' 'heatpump'
          * replacement  (replacement) object 'estove' 'gasboiler' 'gasstove' 'heatpump'
          * timeslice    (timeslice) MultiIndex
          - month        (timeslice) object 'all-year' 'all-year' ... 'all-year'
          - day          (timeslice) object 'all-week' 'all-week' ... 'all-week'
          - hour         (timeslice) object 'night' 'morning' ... 'late-peak' 'evening'
          * commodity    (commodity) object 'cook' 'heat'
            region       <U3 'USA'
            year         ... 2020
            comm_usage   (commodity) ...
        Data variables:
            capacity     (asset, replacement) int64 0 1 2 3 4 5 6 ... 10 11 12 13 14 15
            production   (timeslice, asset, replacement, commodity) float64 0.0 ... 0.0

        The capacity costs correspond exactly to the input costs:

        >>> assert (costs == lpcosts.capacity).all()

        They should correspond to a data-array with dimensions ``(asset, replacement)``
        (and possibly ``region`` as well).

        >>> lpcosts.capacity.dims
        ('asset', 'replacement')

        The production costs are zero by default. However, the production expands over
        not only the dimensions of the capacity, but also the ``timeslice``(s) during
        which production occurs and the ``commodity``(s) produced.

        >>> lpcosts.production.dims
        ('timeslice', 'asset', 'replacement', 'commodity')
    """
    from xarray import zeros_like
    from muse.commodities import is_enduse
    from muse.timeslices import convert_timeslice

    assert "year" not in technologies.dims

    production = zeros_like(
        convert_timeslice(costs, timeslices)
        * technologies.fixed_outputs.sel(
            commodity=is_enduse(technologies.comm_usage),
            technology=technologies.technology.isin(costs.replacement),
        ).rename(technology="replacement")
    )
    return Dataset(dict(capacity=costs, production=production))


def merge_lp(
    costs: Dataset, *constraints: Constraint
) -> Tuple[Dataset, List[Constraint]]:
    """Unify coordinate systems of costs and constraints.

    In practice, this function brings costs and constraints into a single dataset and
    then splits things up again. This ensures the dimensions are not only compatible,
    but also such that that their order in memory is the same.
    """
    from xarray import merge

    data = merge(
        [costs]
        + [
            constraint.rename(
                b=f"b{i}", capacity=f"capacity{i}", production=f"production{i}"
            )
            for i, constraint in enumerate(constraints)
        ]
    )

    unified_costs = cast(Dataset, data[["capacity", "production"]])
    unified_constraints = [
        Dataset(
            {
                "capacity": data[f"capacity{i}"],
                "production": data[f"production{i}"],
                "b": data[f"b{i}"],
            },
            attrs=constraint.attrs,
        )
        for i, constraint in enumerate(constraints)
    ]

    return unified_costs, unified_constraints


def lp_constraint(constraint: Constraint, lpcosts: Dataset) -> Constraint:
    """Transforms the constraint to LP data.

    The goal is to create from ``lpcosts.capacity``, ``constraint.capacity``, and
    ``constraint.b`` a 2d-matrix ``constraint`` vs ``decision variables``.

    #. The dimensions of ``constraint.b`` are the constraint dimensions. They are
        renamed ``"c(xxx)"``.
    #. The dimensions of ``lpcosts`` are the decision-variable dimensions. They are
        renamed ``"d(xxx)"``.
    #. ``set(b.dims).intersection(lpcosts.xxx.dims)`` are diagonal
        in constraint dimensions and decision variables dimension, with ``xxx`` the
        capacity or the production
    #. ``set(constraint.xxx.dims) - set(lpcosts.xxx.dims) - set(b.dims)`` are reduced by
        summation, with ``xxx`` the capacity or the production
    #. ``set(lpcosts.xxx.dims) - set(constraint.xxx.dims) - set(b.dims)`` are added for
        expansion, with ``xxx`` the capacity or the production

    See :py:func:`muse.constraints.lp_constraint_matrix` for a more detailed explanation
    of the transformations applied here.
    """
    b = constraint.b.drop_vars(set(constraint.b.coords) - set(constraint.b.dims))
    b = b.rename({k: f"c({k})" for k in b.dims})
    capacity = lp_constraint_matrix(constraint.b, constraint.capacity, lpcosts.capacity)
    capacity = capacity.drop_vars(set(capacity.coords) - set(capacity.dims))
    production = lp_constraint_matrix(
        constraint.b, constraint.production, lpcosts.production
    )
    production = production.drop_vars(set(production.coords) - set(production.dims))
    return Dataset(
        {"b": b, "capacity": capacity, "production": production}, attrs=constraint.attrs
    )


def lp_constraint_matrix(b: DataArray, constraint: DataArray, lpcosts: DataArray):
    """Transforms one constraint block into an lp matrix.

   The goal is to create from ``lpcosts``, ``constraint``, and ``b`` a 2d-matrix of
   constraints vs decision variables.

    #. The dimensions of ``b`` are the constraint dimensions. They are renamed
        ``"c(xxx)"``.
    #. The dimensions of ``lpcosts`` are the decision-variable dimensions. They are
        renamed ``"d(xxx)"``.
    #. ``set(b.dims).intersection(lpcosts.dims)`` are diagonal
        in constraint dimensions and decision variables dimension
    #. ``set(constraint.dims) - set(lpcosts.dims) - set(b.dims)`` are reduced by
        summation
    #. ``set(lpcosts.dims) - set(constraint.dims) - set(b.dims)`` are added for
        expansion
    #. ``set(b.dims) - set(constraint.dims) - set(lpcosts.dims)`` are added for
        expansion. Such dimensions only make sense if they consist of one point.

    The result is the constraint matrix, expanded, reduced and diagonalized for the
    conditions above.

    Example:

        Lets first setup a constraint and a cost matrix:

        >>> from muse import examples
        >>> from muse import constraints as cs
        >>> res = examples.sector("residential", model="medium")
        >>> technologies = res.technologies
        >>> market = examples.residential_market("medium")
        >>> search = examples.search_space("residential", model="medium")
        >>> assets = next(a.assets for a in res.agents if a.category == "retrofit")
        >>> constraint = cs.max_production(assets, search, market, technologies)
        >>> lpcosts = cs.lp_costs(
        ...     technologies.interp(year=market.year.min() + 5).drop_vars("year"),
        ...     costs=search * np.arange(np.prod(search.shape)).reshape(search.shape),
        ...     timeslices=market.timeslice,
        ... )

        For a simple example, we can first check the case where b is scalar. The result
        ought to be a single row of a matrix, or a vector with only decision variables:

        >>> from pytest import approx
        >>> result = cs.lp_constraint_matrix(
        ...     xr.DataArray(1), constraint.capacity, lpcosts.capacity
        ... )
        >>> assert result.values == approx(1)
        >>> assert set(result.dims) == {f"d({x})" for x in lpcosts.capacity.dims}
        >>> result = cs.lp_constraint_matrix(
        ...     xr.DataArray(1), constraint.production, lpcosts.production
        ... )
        >>> assert set(result.dims) == {f"d({x})" for x in lpcosts.production.dims}
        >>> assert result.values == approx(-1)

        As expected, the cpacicity vector is 1, whereas the production vector is -1.
        These are the values the :py:func:`~muse.constraints.max_production` is set up
        to create.

        Now, let's check the case where ``b`` is the one from the
        :py:func:`~muse.constraints.max_production` constraint. In that case, all the
        dimensions should end up as constraint dimensions: the production for each
        timeslice, region, asset, and replacement technology should not outstrip the
        capacity assigned for the asset and replacement technology.

        >>> result = cs.lp_constraint_matrix(
        ...     constraint.b, constraint.capacity, lpcosts.capacity
        ... )
        >>> decision_dims = {f"d({x})" for x in lpcosts.capacity.dims}
        >>> constraint_dims = {
        ...     f"c({x})" for x in set(lpcosts.production.dims).union(constraint.b.dims)
        ... }
        >>> assert set(result.dims) == decision_dims.union(constraint_dims)

        The :py:func:`~muse.constraints.max_production` constraint on the production
        side is the identy matrix with a factor :math:`-1`. We can easily check this
        by stacking the decision and constraint dimensions in the result:

        >>> result = cs.lp_constraint_matrix(
        ...     constraint.b, constraint.production, lpcosts.production
        ... )
        >>> decision_dims = {f"d({x})" for x in lpcosts.production.dims}
        >>> assert set(result.dims) == decision_dims.union(constraint_dims)
        >>> stacked = result.stack(d=sorted(decision_dims), c=sorted(constraint_dims))
        >>> assert stacked.shape[0] == stacked.shape[1]
        >>> assert stacked.values == approx(-np.eye(stacked.shape[0]))
    """
    from numpy import eye
    from functools import reduce

    result = constraint.sum(set(constraint.dims) - set(lpcosts.dims) - set(b.dims))
    result = result.rename(
        {k: f"d({k})" for k in set(result.dims).intersection(lpcosts.dims)}
    )
    result = result.rename(
        {k: f"c({k})" for k in set(result.dims).intersection(b.dims)}
    )

    expand = set(lpcosts.dims) - set(constraint.dims) - set(b.dims)
    result = result.expand_dims({f"d({k})": lpcosts[k] for k in expand})
    expand = set(b.dims) - set(constraint.dims) - set(lpcosts.dims)
    result = result.expand_dims({f"c({k})": b[k] for k in expand})

    diag_dims = set(b.dims).intersection(lpcosts.dims)
    if diag_dims:

        def get_dimension(dim):
            if dim in b.dims:
                return b[dim].values
            if dim in lpcosts.dims:
                return lpcosts[dim].values
            return constraint[dim].values

        diagonal_submats = [
            DataArray(
                eye(len(b[k])),
                coords={f"c({k})": get_dimension(k), f"d({k})": get_dimension(k)},
                dims=(f"c({k})", f"d({k})"),
            )
            for k in diag_dims
        ]
        result = result * reduce(DataArray.__mul__, diagonal_submats)
    return result


def scipy_adapter(
    technologies: Dataset, costs: DataArray, timeslices, *constraints: Constraint
):
    """Creates the input for the scipy solvers.

    Example:

        Lets give a fist simple example. The constraint
        :py:func:`~muse.constraints.max_capacity_expansion` limits how much each
        capacity can be expanded in a given year.

        >>> from muse import examples
        >>> from muse import constraints as cs
        >>> res = examples.sector("residential", model="medium")
        >>> market = examples.residential_market("medium")
        >>> search = examples.search_space("residential", model="medium")
        >>> assets = next(a.assets for a in res.agents if a.category == "retrofit")
        >>> costs = search * np.arange(np.prod(search.shape)).reshape(search.shape)
        >>> constraint = cs.max_capacity_expansion(
        ...     assets, search, market, res.technologies,
        ... )
        >>> constraint
        <xarray.Dataset>
        Dimensions:      (region: 1, replacement: 4)
        Coordinates:
          * region       (region) object 'USA'
            technology   (replacement) object 'estove' 'gasboiler' 'gasstove' 'heatpump'
          * replacement  (replacement) object 'estove' 'gasboiler' 'gasstove' 'heatpump'
        Data variables:
            b            (replacement, region) float64 500.0 500.0 500.0 500.0
            capacity     ... 1
            production   ... 0
        Attributes:
            kind:     ConstraintKind.UPPER_BOUND

        As shown above, it does not bind the production decision variables. Hence,
        production is zero. The matrix operator for the capacity is simply the identity.
        Hence it can be inputed as the dimensionless scalar 1. The upper bound is simply
        the maximum for replacement technology (and region, if that particular dimension
        exists in the problem).

        The lp problem then becomes:

        >>> technologies = res.technologies.interp(year=market.year.min() + 5)
        >>> inputs = cs.scipy_adapter(
        ...     technologies, costs, market.timeslice, constraint
        ... )

        The decision variables are always constrained between zero and infinity:

        >>> assert inputs["bounds"] == (0, None)

        The problem is an upper-bound one. There are no equality constraints:

        >>> assert inputs["A_eq"] is None
        >>> assert inputs["b_eq"] is None

        The upper bound matrix and vector, and the costs are consistent in their
        dimensions:

        >>> assert inputs["c"].ndim == 1
        >>> assert inputs["b_ub"].ndim == 1
        >>> assert inputs["A_ub"].ndim == 2
        >>> assert inputs["b_ub"].size == inputs["A_ub"].shape[0]
        >>> assert inputs["c"].size == inputs["A_ub"].shape[1]
        >>> assert inputs["c"].ndim == 1

        In practice, :py:func:`~muse.constraints.lpcosts` helps us define the decision
        variables (and ``c``). We can verify that the sizes are consistent:

        >>> lpcosts = cs.lp_costs(technologies, costs, market.timeslice)
        >>> capsize = lpcosts.capacity.size
        >>> prodsize = lpcosts.production.size
        >>> assert inputs["c"].size == capsize + prodsize

        The upper bound itself is over each replacement technology:

        >>> assert inputs["b_ub"].size == lpcosts.replacement.size

        The production decision variables are not involved:

        >>> from pytest import approx
        >>> assert inputs["A_ub"][:, capsize:] == approx(0)

        The matrix for the capacity decision variables is a sum over assets for a given
        replacement technology. Hence, each row is constituted of zeros and ones and
        sums to the number of assets:

        >>> assert inputs["A_ub"][:, :capsize].sum(axis=1) == approx(lpcosts.asset.size)
        >>> assert set(inputs["A_ub"][:, :capsize].flatten()) == {0.0, 1.0}
    """
    from xarray import merge
    from pandas import MultiIndex
    from numpy import concatenate, ndarray

    assert "year" not in technologies.dims
    lpcosts = lp_costs(technologies, costs, timeslices)
    data = merge(
        [lpcosts.rename({k: f"d({k})" for k in lpcosts.dims})]
        + [
            lp_constraint(constraint, lpcosts).rename(
                b=f"b{i}", capacity=f"capacity{i}", production=f"production{i}"
            )
            for i, constraint in enumerate(constraints)
        ]
    )
    for i, constraint in enumerate(constraints):
        if constraint.kind == ConstraintKind.LOWER_BOUND:
            data[f"b{i}"] = -data[f"b{i}"]  # type: ignore
            data[f"capacity{i}"] = -data[f"capacity{i}"]  # type: ignore
            data[f"production{i}"] = -data[f"production{i}"]  # type: ignore

    data = data.set_index(
        {
            dim: list(data.get_index(dim))
            for dim in data.dims
            if isinstance(data.get_index(dim), MultiIndex)
        }
    )

    def extract(data, name):
        result = data[[u for u in data.data_vars if u.startswith(name)]]
        return result.rename(
            {
                k: ("costs" if k == name else int(k.replace(name, "")))
                for k in result.data_vars
            }
        )

    bs = extract(data, "b")

    capacities = extract(data, "capacity")
    capacities = capacities.stack(decision=sorted(capacities.costs.dims))

    productions = extract(data, "production")
    productions = productions.stack(decision=sorted(productions.costs.dims))

    c = concatenate((capacities["costs"].values, productions["costs"].values), axis=0)

    def extract_bA(*kinds):
        indices = [i for i in range(len(bs)) if constraints[i].kind in kinds]
        capa_constraints = [
            capacities[i]
            .stack(constraint=sorted(bs[i].dims))
            .transpose("constraint", "decision")
            .values
            for i in indices
        ]
        prod_constraints = [
            productions[i]
            .stack(constraint=sorted(bs[i].dims))
            .transpose("constraint", "decision")
            .values
            for i in indices
        ]
        if capa_constraints:
            A: Optional[ndarray] = concatenate(
                (
                    concatenate(capa_constraints, axis=0),
                    concatenate(prod_constraints, axis=0),
                ),
                axis=1,
            )
            b: Optional[ndarray] = concatenate(
                [bs[i].stack(constraint=sorted(bs[i].dims)) for i in indices], axis=0
            )
        else:
            A = None
            b = None
        return A, b

    A_ub, b_ub = extract_bA(ConstraintKind.UPPER_BOUND, ConstraintKind.LOWER_BOUND)
    A_eq, b_eq = extract_bA(ConstraintKind.EQUALITY)

    return {
        "c": c,
        "A_ub": A_ub,
        "b_ub": b_ub,
        "A_eq": A_eq,
        "b_eq": b_eq,
        "bounds": (0, None),
    }
