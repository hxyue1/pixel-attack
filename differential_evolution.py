import numpy as np
import torch

"""
Module for performing differential evolution to hack image classifiers.

See:

https://arxiv.org/abs/1710.08864
https://en.wikipedia.org/wiki/Differential_evolution
"""

def generate_children(parent_candidates, cr_prob, F, round_=False):
    """Generates children to compete with parents as candidates for the next generation.

    Args:
        parent_candidates (numpy.array): Array of either pixel coordinates or intensities for 
            the parents. Should be of shape (num_candidates, num_pixels, dim). Where num_candidates 
            is the number of agents in each generation, num_pixels is the number of pixels being 
            altered, and dim is either 2 for the pixel location (x, y) or the number of channels 
            in the image (usually 3).
        cr_prob (float): The crossover probability. This is the probability of updating a child.
            In Su et al (2017) this is set to 0.9.
        F (float): The differential weight. To generate a candidate child, we take three parents a, b and c
            and take a linear combination a + F*(b-c) where F is the differential weight.
        round_ (boolean): Whether to round the linear combination  a + F*(b-c) to the nearest integer.
            This is needed when generating x, y location pairs.
    
    Returns:
        children (numpy.array): The array of children who compete with parents in the next generation.
            Should be of the same shape as the parents.
    """
    children = []
    num_pixels = parent_candidates.shape[1]
    for i in range(0, len(parent_candidates)):

        # Sampling parent candidates for each child
        # For the ith child, we remove the ith parent from the pool of potential parents
        parent_candidates_ = np.delete(parent_candidates, i, 0)
        parent_idx = np.random.choice(len(parent_candidates_), size=3, replace=True)
        parents = parent_candidates_[parent_idx]

        # Potential update
        # If values are pixel coordinate, then they needed to be rounded to an integer 
        if round_ == True:
            potential_child = np.rint(parents[0] + F*(parents[1] - parents[2])).astype(int)
        else:
            potential_child = parents[0] + F*(parents[1] - parents[2])

        # Mask controls probability of update for each dimension of child
        mask = np.random.uniform(size=num_pixels) < cr_prob
        # Update values
        child = np.where(np.expand_dims(mask, 1), potential_child, parent_candidates[i])
        children.append(child)

    # Convert to array
    children = np.stack(children)
    return children

def generate_image_variants(img, all_candidate_coords, all_candidate_pixel_vals):
    """Generates variants of target image to be passed to the model for evaluation.

    Args:
        img (torch.tensor): A single image in tensor form. Should be of shape (channels, H, W)
        all_candidate_coords (np.array): Array of pixel locations to be modified.  Must be of shape
            (num_candidates, num_pixels, 2). Where num_candidates is the number of agents 
            in each generation, pixels is the number of pixels being altered by an agent.
         all_candidate_pixel_vals (np.array): Array of pixel intensities corresponding to coords to be modified.  
            Must be of shape (num_candidates, pixels, 3). Where num_candidates is the number of agents 
            in each generation, num_pixels is the number of pixels being altered by an agent.
    Returns:
        new_imgs (torch.tensor): A tensor of image variants of shape (num_candidates, channels, H, W).
    """
    # First two dimensions of coords and pixel_vals must match i.e. (num_candidates, num_pixels)
    assert all_candidate_coords.shape[0:2] == all_candidate_pixel_vals.shape[0:2]
    num_candidates = len(all_candidate_coords)
    # Modifying the original image multiple times 
    new_imgs = img.detach().repeat(num_candidates, 1, 1, 1)
    # Looping through each candidate
    for i, (pixel_locs, pixel_vals) in enumerate(zip(all_candidate_coords, all_candidate_pixel_vals)):
        # Looping through each pixel
        for j, xy_pair in enumerate(pixel_locs):
            x = xy_pair[0]
            y = xy_pair[1]
            pixel_val = pixel_vals[j] # 1 dimensional array with values for each channel e.g. [R:0.1, G:0.7, B:0.9]
            # Update image
            new_imgs[i, :, x, y] = torch.from_numpy(pixel_val)
    return new_imgs

def evaluation_step(model, parent_imgs, child_imgs, target_class, actual_class):
    """Evaluates image variants generated by parents vs children.

    Args:
        model (torch.nn.Module): A trained image classifier which returns logits for each image class.
        parent_imgs (torch.Tensor): The image variants generated by the parent agents
            Should be of shape (num_candidates, channels, height, width).
        child_imgs (torch.Tensor): The image variants generated by the child agents.
            Should be of shape (num_candidates, channels, height, width).
        target_class (int): The index of the target class which we want to maximise logits for.
        actual_class (int): The actual correct image class which we want to minimise logits for.
    """
    # No need to backpropagate gradients
    with torch.no_grad():
        # Moving model and images to cpu since we may be constrained by GPU ram.
        model = model.to('cpu')
        parent_imgs = parent_imgs.to('cpu')
        child_imgs = child_imgs.to('cpu')
        
        # Calculating logits of image variants
        parent_logits = model(parent_imgs)
        child_logits = model(child_imgs)
    
    ## Calculating divergence of target class logit from actual class logit (bigger is better)
    parent_divergence = parent_logits[:, target_class] - parent_logits[:, actual_class]
    child_divergence = child_logits[:, target_class] - child_logits[:, actual_class]
    
    # The decrease in actual class logits (bigger is better)
    actual_class_degradation = parent_logits[:, actual_class] - child_logits[:, actual_class]
    
    # Where have the logits improved for the target class ?
    # Note we use >= and not just > because even if the result is the same, variability for it's own
    # sake is valuable. Try changing it to > to see how it affects the differential evolution algorithm
    update_mask = torch.where(child_divergence >= parent_divergence , True, False)

    # The updated logits for the target and actual class
    new_target_logits = torch.where(update_mask, child_logits[:, target_class], parent_logits[:, target_class])
    new_actual_logits = torch.where(update_mask, child_logits[:, actual_class], parent_logits[:, actual_class])

    # Bigger is better
    best_target_logit = new_target_logits.argmax()
    # Smaller is better
    best_actual_logit = new_actual_logits.argmin()

    # The best agent for the target class may not be the best agent for the actual class
    print('Best logit for the target class: {:.4f}'.format(new_target_logits[best_target_logit]))
    print('Corresponding logit for the actual class: {:.4f}'.format(new_actual_logits[best_target_logit]))
    print('Best logit for the actual class: {:.4f}'.format(new_actual_logits[best_actual_logit]))
    print('Corresponding logit for the target class: {:.4f}'.format(new_target_logits[best_actual_logit]))
    print('')

    return update_mask, new_target_logits, new_actual_logits

def stopping_criterion(target_logits, actual_logits):
    """Checks if target class logits have exceed the actual class for any agent in the population.

    Args:
        target_logits (torch.Tensor): A tensor of logits for the target class. Should be of shape (num_candidates,)
        actual_logits (torch.Tensor): A tensor of logits for the actual class. Should be of shape (num_candidates,)
    
    Returns:
        stop (bool): Whether to stop the evolution process.
        best_agent (int): The index or indices of the best agent.
    """

    if (target_logits > actual_logits).any():
        stop = True
        best_agent = (target_logits > actual_logits).nonzero()
    else:
        stop = False
        best_agent = None
    
    return stop, best_agent

def evolution_step(model, img, parent_coords, parent_pixel_vals, target_class, actual_class, cr_prob=0.9, F=0.5):
    """Runs an entire step of the differential evolution algorithm.

    Args:
        model (torch.nn.Module): A trained image classifier which returns logits for each image class.
        img (torch.Tensor): The target image to perturb. Should be of shape (channels, height, width).
        parent_coords (np.array): The xy pixel locations to be modified. Should be of shape 
            (num_candidates, num_pixels, 2).
        parent_pixel_vals (np.array): The pixel intensities for the given locations xy locations. 
            Should be of shape (num_candidates, num_pixels, 3).
        target_class (int): The index of the target class which we want to maximise logits for.
        actual_class (int): The actual correct image class which we want to minimise logits for.
        cr_prob (float): The crossover probability. 
        F (float): The differential weight.
    """
    # Todo: generate in tanh space to avoid boundary problems
    # Generate children
    child_coords = generate_children(parent_coords, cr_prob, F, round_=True)
    child_pixel_values = generate_children(parent_pixel_vals, cr_prob, F, round_=False)

    # Todo: allow for images with width != height
    image_size = img.shape[1]

    # Replace negative values and values greater than image size
    child_coords = np.where(child_coords < 0, -child_coords, child_coords)
    child_coords = np.where(child_coords > image_size-1, image_size-1-child_coords, child_coords)

    # Replace negative values and values greater than 1 for pixel values
    child_pixel_values = np.where(child_pixel_values < 0, -child_pixel_values, child_pixel_values)
    child_pixel_values = np.where(child_pixel_values > 1, 1.0-child_pixel_values, child_pixel_values)

    # Generate image variants
    parent_imgs = generate_image_variants(img, parent_coords, parent_pixel_vals)
    child_imgs = generate_image_variants(img, child_coords, child_pixel_values)

    # Parents vs children to keep
    update_mask, new_target_logits, new_actual_logits = evaluation_step(model, parent_imgs, child_imgs, target_class, actual_class)
    stop, best_agent = stopping_criterion(new_target_logits, new_actual_logits)

    next_gen_coords = np.where(np.expand_dims(update_mask.numpy(), (1,2)), child_coords, parent_coords)
    next_gen_pixel_vals = np.where(np.expand_dims(update_mask.numpy(), (1,2)), child_pixel_values, parent_pixel_vals)

    return update_mask, next_gen_coords, next_gen_pixel_vals, stop, best_agent