import React, {useState} from "react";
import {handle_http_errors, postData, showResponse} from "../../utils/fetchUtils";
import {Button, FormControl, FormControlLabel, FormLabel, Grid, Radio, RadioGroup} from "@material-ui/core";
 const ShuffleControls = () => {
    /* TODO: should make an endpoint to provide these dynamically */
    const METHODS = [
        {method: "random_shuffle", label: "Random Shuffle"},
        {method: "player_level", label: "By Player Level"},
        {method: "split_shuffle", label: "Split Shuffle"},
    ];

    const [shuffleMethod, setShuffleMethod] = useState(METHODS[0].method);

    const handleChange = (e) => setShuffleMethod(e.target.value);
    const handleSubmit = (e) => {
        e.preventDefault();
        return postData(`${process.env.REACT_APP_API_URL}do_shuffle_teams`, {
            shuffle_method: shuffleMethod,
        })
            .then((response) =>
                showResponse(response, `Team Shuffle by ${shuffleMethod}`, true)
            )
            .catch(handle_http_errors);
    };

    return (
        <Grid item xs={12} lg={6}>
            <form>
                <FormControl>
                    <FormLabel>Shuffle Controls</FormLabel>
                    <RadioGroup
                        name="shuffle_method"
                        value={shuffleMethod}
                        onChange={handleChange}
                    >
                        {METHODS.map((m) => {
                            return (
                                <FormControlLabel
                                    value={m.method}
                                    control={<Radio/>}
                                    label={m.label}
                                />
                            );
                        })}
                    </RadioGroup>
                </FormControl>
                <Button type="submit" variant="contained" onClick={handleSubmit}>
                    Shuffle Teams
                </Button>
            </form>
        </Grid>
    );
};

 export default ShuffleControls;