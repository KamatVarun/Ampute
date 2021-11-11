def ord_to_interval(column: pd.Series,
                    column_name: str = 'ordinal',
                    order: list = None,
                    proportion_based: bool = False,
                    relation=lambda x: x,
                    inverse_relation=None):
    if not order:
        order = list(set(column))

    if proportion_based:
        counts = dict(column.value_counts())
    else:
        counts = dict.fromkeys(set(column), math.floor(len(column)/len(set(column))))

    init = 0
    ranges_start = {}
    ranges_end = {}
    for i in order:
        compute = relation(counts[i])
        ranges_start[i] = init
        ranges_end[i] = init+compute
        init = init+compute

    return pd.DataFrame([column.map(ranges_start), column.map(ranges_end)],
                        ['_start_'+column_name, '_end_'+column_name]).transpose()