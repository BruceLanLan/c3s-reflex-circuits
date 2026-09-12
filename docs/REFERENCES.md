# References

Only sources that were actually consulted are listed. Where only an abstract was
available, that is stated, and no figures beyond the abstract are attributed to
the paper anywhere in this repository.

## Connectome data and tools

1. **Berg S, Beckett IR, Costa M, Schlegel P, Januszewski M, et al.** Sexual
   dimorphism in the complete *Drosophila* male central nervous system connectome.
   *Cell* (2026). doi:10.1016/j.cell.2026.08.015. — Source of the MaleCNS v1.0
   data used here.
2. **Januszewski M, Jain V.** A connectomics milestone: mapping the complete male
   fruit fly brain. Google Research blog, 3 September 2026. — Describes Google
   Research's contribution: flood-filling network segmentation, the PATHFINDER
   reconstruction system and Neuroglancer.
3. **MaleCNS release bucket** `gs://flyem-male-cns/v1.0/` and download page at
   male-cns.janelia.org (licence: CC-BY).

## Escape circuit

4. **Ache JM, Polsky J, Alghailani S, Parekh R, Breads P, Peek MY, Bock DD,
   von Reyn CR, Card GM.** Neural basis for looming size and velocity encoding in
   the *Drosophila* giant fiber escape pathway. *Current Biology* 29:1073–1081
   (2019). doi:10.1016/j.cub.2019.01.079. *Abstract consulted.*
5. **von Reyn CR, Breads P, Peek MY, Zheng GZ, Williamson WR, Yee AL, Leonardo A,
   Card GM.** A spike-timing mechanism for action selection. *Nature
   Neuroscience* 17:962–970 (2014). doi:10.1038/nn.3741. *Abstract consulted.*
6. **von Reyn CR, Nern A, Williamson WR, Breads P, Wu M, Namiki S, Card GM.**
   Feature integration drives probabilistic behavior in the *Drosophila* escape
   response. *Neuron* 94:1190–1204 (2017). doi:10.1016/j.neuron.2017.05.036.
   *Abstract consulted.*

## Limits of connectome-based models

7. **Scheffer LK, Meinertzhagen IA.** A connectome is not enough — what is still
   needed to understand the brain of *Drosophila*? *Journal of Experimental
   Biology* 224:jeb242740 (2021). doi:10.1242/jeb.242740. *Abstract consulted.*
8. **Pospisil DA, Aragon MJ, Dorkenwald S, et al.** The fly connectome reveals a
   path to the effectome. *Nature* 634:201–209 (2024).
   doi:10.1038/s41586-024-07982-0. *Abstract consulted.*

## Logic gate networks

9. **Petersen F, Borgelt C, Kuehne H, Deussen O.** Deep differentiable logic gate
   networks. *NeurIPS* 2022. arXiv:2210.08277. — The method re-implemented in
   `c3s/dlgn.py`. Reference implementation: difflogic (MIT License; README states
   "Patent pending").
10. **Petersen F, Kuehne H, Borgelt C, Welzel J, Ermon S.** Convolutional
    differentiable logic gate networks. *NeurIPS* 2024. arXiv:2411.04732. —
    Source of the residual (pass-through) initialisation option.
11. **Miotti P, Niklasson E, Randazzo E, Mordvintsev A.** Differentiable logic
    cellular automata: from Game of Life to pattern generation. Google Research,
    Paradigms of Intelligence team (2025). arXiv:2506.04912. — First use of
    differentiable logic gates in recurrent circuits; the conceptual precedent for
    learning *stateful* logic, which the escape core's latches implement by hand
    here.

## Synthesis

12. **Berkeley Logic Synthesis and Verification Group.** ABC: a system for
    sequential synthesis and verification. Used as distributed with Yosys 0.68.
