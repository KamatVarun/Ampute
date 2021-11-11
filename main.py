import generator
import pandas as pd
import newampute
import numpy as np

# generating data
col = generator.column(datatype='ordinal', classes=6, proportions=[2, 5, 2, 1, 4, 3])
sample1 = pd.Series(col.generate_nominal_column(1000))
sample11 = pd.Series(col.generate_nominal_column(1000))
col2 = generator.column(datatype='numerical', classes=6, proportions=[2, 5, 2, 1, 4, 3])
sample2 = pd.Series(col2.generate_numerical_column(1000,3,5))
sample3 = pd.Series(col2.generate_numerical_column(1000,3,3))
sample4 = pd.Series(col2.generate_numerical_column(1000,2,3))
simple = pd.DataFrame([sample2,sample4,sample3]).transpose()

# settings for the ampute function
pattern = np.array([[0,1,1,1,1],[1,0,1,1,1],[1,1,0,1,1],[1,1,1,0,1],[1,1,1,1,0]])
weights = np.array([[0,1,1,1,1],[1,0,1,1,1],[1,1,0,1,1],[1,1,1,0,1],[1,1,1,1,0]])
mechanism = ['MAR']
frequencies = [0.1, 0.2, 0.1, 0.5, 0.1]
amp_type = ['RIGHT']
hos = pd.DataFrame([sample2,sample4,sample3,sample1,sample11]).transpose()

# doing the ampute function with ordinal data.
print(newampute.MultivariateAmputation(patterns=pattern,
                                       prop=0.7,
                                       weights=weights,
                                       mechanisms=mechanism,
                                       freqs=frequencies,
                                       types=amp_type
                                       )
      .do_ordinal_ampute(ord_cols=[3, 4],
                         data=hos,
                         orders=[['rank0', 'rank1', 'rank2', 'rank3', 'rank4', 'rank5'],['rank0', 'rank1', 'rank2', 'rank3', 'rank4', 'rank5']],
                         lproportion_based=[True, True],
                         relations=[lambda x: x, lambda x: x],
                         norm = 1))




