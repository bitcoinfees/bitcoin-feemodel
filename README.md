NOTE: This project has been succeeded by [Feesim](https://github.com/bitcoinfees/feesim).

bitcoin-feemodel (unreleased)
-----------------------------

Model-based transaction fee estimation; this is a work in progress.

The general approach is:

1. Model a miner's transaction selection policy as greedily adding txs
   according to feerate until max block size or min fee rate has been reached,
   whichever is earlier.
2. Model the transaction arrivals as a poisson process, with each
   transaction having (feerate, size) independently drawn from a certain
   joint distribution.
3. Estimate the model parameters and run a simulation to obtain the metrics of
   interest e.g. the wait / confirmation time of a transaction as a function of
   its fee rate.
